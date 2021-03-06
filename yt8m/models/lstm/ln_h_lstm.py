# Copyright 2016 Google Inc. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS-IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Contains a collection of models which operate on variable-length sequences.
"""
import math

import tensorflow as tf

import tensorflow.contrib.slim as slim

from tensorflow.contrib.rnn.python.ops import core_rnn_cell
from yt8m.models import models
import yt8m.models.model_utils as utils
from yt8m.models.lstm import LNGRUCell
from tensorflow.contrib.cudnn_rnn.python.ops import cudnn_rnn_ops
from tensorflow.contrib.rnn.python.ops import gru_ops
from yt8m.starter import video_level_models

slim = tf.contrib.slim

class LNHGRUncoder(models.BaseModel):
  def __init__(self):
    super(LNHGRUncoder, self).__init__()

    self.normalize_input = True # TODO
    self.clip_global_norm = 5
    self.var_moving_average_decay = 0.9997
    self.optimizer_name = "AdamOptimizer"
    self.base_learning_rate = 5e-4

    self.cell_size = 1024
    self.max_steps = 300
    # TODO
    self.decay_lr = True

  def create_model(self, model_input, vocab_size, num_frames,
                   is_training=True, dense_labels=None, **unused_params):
    self.phase_train = is_training
    num_frames = tf.cast(tf.expand_dims(num_frames, 1), tf.float32)

    # dec_cell = self.get_dec_cell(self.cell_size)
    runtime_batch_size = tf.shape(model_input)[0]

    moe = video_level_models.MoeModel()
    with tf.variable_scope("EncLayer0"):
      first_enc_cell = self.get_enc_cell(self.cell_size, vocab_size) # TODO
      enc_init_state = tf.zeros((runtime_batch_size, self.cell_size), dtype=tf.float32)
      num_splits = 15
      model_input_splits = tf.split(model_input, num_or_size_splits=num_splits, axis=1)
      enc_state = None
      first_layer_outputs = []
      for i in xrange(num_splits):
        if i == 0:
          initial_state = enc_init_state
        else:
          initial_state = enc_state
          tf.get_variable_scope().reuse_variables()
        initial_state = tf.stop_gradient(initial_state)
        enc_outputs, enc_state = tf.nn.dynamic_rnn(
            first_enc_cell, model_input_splits[i], initial_state=initial_state, scope="enc0")
        # TODO
        enc_state = moe.moe_layer(enc_state, 1024, 4, act_func=None, l2_penalty=1e-12)
        if is_training:
          enc_state = tf.nn.dropout(enc_state, 0.5)
        first_layer_outputs.append(enc_state)

    with tf.variable_scope("EncLayer1"):
      second_enc_cell = self.get_enc_cell(self.cell_size, vocab_size)
      enc_init_state = tf.zeros((runtime_batch_size, self.cell_size), dtype=tf.float32)
      first_layer_outputs = tf.stack(first_layer_outputs, axis=1)
      enc_outputs, enc_state = tf.nn.dynamic_rnn(
          second_enc_cell, first_layer_outputs, initial_state=enc_init_state, scope="enc1")
    # TODO
    if is_training:
      enc_state = tf.nn.dropout(enc_state, 0.8)
    logits = moe.moe_layer(enc_state, vocab_size, 2, act_func=tf.nn.sigmoid, l2_penalty=1e-8)
    return {"predictions": logits}

  def get_enc_cell(self, cell_size, vocab_size):
    from tensorflow.contrib.layers.python.layers import initializers
    uniform_initializer = initializers.xavier_initializer()
    # uniform_init_scale = 0.01
    # uniform_initializer = tf.random_uniform_initializer(
        # minval=-uniform_init_scale,
        # maxval=uniform_init_scale)

    def random_orthonormal_initializer(shape, dtype=tf.float32,
                                      partition_info=None):  # pylint: disable=unused-argument
      """Variable initializer that produces a random orthonormal matrix."""
      if len(shape) != 2 or shape[0] != shape[1]:
        raise ValueError("Expecting square shape, got %s" % shape)
      _, u, _ = tf.svd(tf.random_normal(shape, dtype=dtype), full_matrices=True)
      return u


    cell = LNGRUCell.LayerNormGRUCell(
        cell_size,
        w_initializer=uniform_initializer,
        u_initializer=random_orthonormal_initializer,
        b_initializer=tf.constant_initializer(0.0))
    return cell

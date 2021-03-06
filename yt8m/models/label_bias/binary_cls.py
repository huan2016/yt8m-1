import math

import tensorflow as tf

import tensorflow.contrib.slim as slim

from tensorflow.contrib.rnn.python.ops import core_rnn_cell
from tensorflow.contrib.legacy_seq2seq.python.ops import seq2seq as seq2seq_lib
from tensorflow.contrib.rnn.python.ops import core_rnn_cell_impl

from yt8m.models import models
import yt8m.models.model_utils as utils

linear = core_rnn_cell_impl._linear  # pylint: disable=protected-access

class BinaryLogisticModel(models.BaseModel):
  def __init__(self):
    super(BinaryLogisticModel, self).__init__()
    self.optimizer_name = "AdamOptimizer"
    self.base_learning_rate = 1e-2
    self.num_classes = 1
    # TODO
    self.normalize_input = False
    self.use_vlad = True


  def create_model_moe(self, model_input, vocab_size, l2_penalty=1e-5,
                   is_training=True, dense_labels=None, **unused_params):
    num_centers = []
    model_input = tf.reshape(model_input, [-1, 256, 256])
    model_input = self.normalize(model_input)
    model_input_4d = tf.reshape(model_input, [-1, 256, 1, 256])
    gate_activations = slim.conv2d(
        model_input_4d,
        1, [1, 1], activation_fn=None, biases_initializer=None,
        weights_regularizer=slim.l2_regularizer(1e-8),
        scope="gates")
    gating_distribution = tf.nn.softmax(gate_activations, dim=1)

    expert_activations = slim.conv2d(
        model_input_4d,
        1, [1, 1], activation_fn=None, biases_initializer=None,
        weights_regularizer=slim.l2_regularizer(1e-8),
        scope="experts")
    expert_distribution = tf.nn.sigmoid(expert_activations)
    predictions = tf.reduce_sum(gating_distribution * expert_distribution, axis=[1, 2])

    epsilon = 1e-12
    labels = tf.cast(dense_labels, tf.float32)
    cross_entropy_loss = labels * tf.log(predictions + epsilon) + (
        1 - labels) * tf.log(1 - predictions + epsilon)
    cross_entropy_loss = tf.negative(cross_entropy_loss)
    loss = tf.reduce_mean(tf.reduce_sum(cross_entropy_loss, 1))

    return {"predictions": predictions, "loss": loss}

  # create_model_dropout
  def create_model(self, model_input, vocab_size, l2_penalty=1e-5,
                   is_training=True, dense_labels=None, **unused_params):
    model_input = self.normalize(model_input)
    model_input = tf.reshape(model_input, [-1, 256, 256])
    sentinal = tf.ones((1, 256, 1), dtype=tf.float32)
    dropout_ratio = 2
    if is_training:
      sentinal = tf.nn.dropout(sentinal, 1./dropout_ratio)
    model_input = model_input * sentinal
    logits = tf.reshape(model_input, [-1, 256 * 256])
    # if not is_training:
      # logits /= dropout_ratio

    logits = slim.fully_connected(
        logits, 1024, activation_fn=None,
        weights_regularizer=slim.l2_regularizer(1e-8))
    if is_training:
      sentinal = tf.nn.dropout(logits, 1./dropout_ratio)

    logits = slim.fully_connected(
        logits, 1, activation_fn=None,
        weights_regularizer=slim.l2_regularizer(1e-8))
    labels = tf.cast(dense_labels, tf.float32)
    loss = tf.nn.sigmoid_cross_entropy_with_logits(labels=labels, logits=logits)
    loss = tf.reduce_mean(tf.reduce_sum(loss, 1))
    preds = tf.nn.sigmoid(logits)
    return {"predictions": preds, "loss": loss}

  def create_model_matrix(self, model_input, vocab_size, l2_penalty=1e-5,
                   is_training=True, dense_labels=None, **unused_params):
    att_hidden_size = 100
    hidden = slim.conv2d(model_input, att_hidden_size, [1, 1], activation_fn=None, scope="hidden_conv2d")
    v = tf.get_variable("attn_v", [1, 1, 1, att_hidden_size],
                        initializer=tf.constant_initializer(0.0))
    fea_size = 256
    C = 256

    def attn(query):
      query = tf.reshape(query, [-1, fea_size])
      y = linear(query, att_hidden_size, True, 0.0)
      y = tf.reshape(y, [-1, 1, 1, att_hidden_size])
      o = tf.reduce_sum(v * tf.tanh(hidden + y), [2, 3])
      o = tf.reshape(o, [-1, C])
      a = tf.nn.softmax(o)
      d = tf.reduce_sum(
          tf.reshape(a, [-1, C, 1, 1]) * hidden, [1, 2])
      return

    for i in xrange(10):
      pass

    # if is_training:
      # model_input = tf.nn.dropout(model_input, 0.2)
    l2_penalty = 1e-12
    logits = slim.fully_connected(
        model_input, 1, activation_fn=None,
        weights_regularizer=slim.l2_regularizer(l2_penalty))
    labels = tf.cast(dense_labels, tf.float32)
    loss = tf.nn.sigmoid_cross_entropy_with_logits(labels=labels, logits=logits)
    loss = tf.reduce_mean(tf.reduce_sum(loss, 1))
    preds = tf.nn.sigmoid(logits)
    return {"predictions": preds, "loss": loss}

  def create_model_video(self, model_input, vocab_size, l2_penalty=1e-5,
                   is_training=True, dense_labels=None, **unused_params):
    l2_penalty = 1e-8
    logits = slim.fully_connected(
        model_input, 1, activation_fn=None,
        weights_regularizer=slim.l2_regularizer(l2_penalty))
    labels = tf.cast(dense_labels, tf.float32)
    loss = tf.nn.sigmoid_cross_entropy_with_logits(labels=labels, logits=logits)
    loss = tf.reduce_mean(tf.reduce_sum(loss, 1))
    preds = tf.nn.sigmoid(logits)
    return {"predictions": preds, "loss": loss}

  def create_model_vlad(self, model_input, vocab_size, l2_penalty=1e-5,
                   is_training=True, dense_labels=None, **unused_params):
    '''
    output = slim.fully_connected(
        model_input, 4096, activation_fn=tf.nn.relu,
        weights_regularizer=slim.l2_regularizer(l2_penalty))
    output = slim.fully_connected(
        output, 4096, activation_fn=tf.nn.relu,
        weights_regularizer=slim.l2_regularizer(l2_penalty))
    output = slim.fully_connected(
        output, vocab_size, activation_fn=tf.nn.sigmoid,
        weights_regularizer=slim.l2_regularizer(l2_penalty))
    '''

    # if is_training:
      # model_input = tf.nn.dropout(model_input, 0.2)
    l2_penalty = 1e-8
    logits = slim.fully_connected(
        model_input, 1, activation_fn=None,
        weights_regularizer=slim.l2_regularizer(l2_penalty))
    labels = tf.cast(dense_labels, tf.float32)
    # TODO
    # labels = tf.abs(labels - 0.1)
    loss = tf.nn.sigmoid_cross_entropy_with_logits(labels=labels, logits=logits)
    loss = tf.reduce_mean(tf.reduce_sum(loss, 1))
    preds = tf.nn.sigmoid(logits)
    return {"predictions": preds, "loss": loss}

  def normalize(self, model_input):
    if self.use_vlad:
      model_input = tf.sign(model_input) * tf.sqrt(tf.abs(model_input))
      model_input = tf.reshape(model_input, [-1, 256, 256])
      model_input = tf.nn.l2_normalize(model_input, 2)
      model_input = tf.reshape(model_input, [-1, 256*256])
      model_input = tf.nn.l2_normalize(model_input, 1)
    return model_input

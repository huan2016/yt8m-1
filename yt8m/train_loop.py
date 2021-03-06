import time
import os

import tensorflow as tf
from tensorflow import gfile
from tensorflow import logging
from yt8m.evaluation import eval_util
import utils

slim = tf.contrib.slim

def supervised_tasks(self, sv, res):
  global_step = res["global_step"]
  predictions = res["predictions"]
  if type(predictions) == list:
    predictions = eval_util.transform_preds(self, predictions)
  dense_labels = res["dense_labels"]

  hit_at_one = eval_util.calculate_hit_at_one(predictions, dense_labels)
  perr = eval_util.calculate_precision_at_equal_recall_rate(predictions,
                                                            dense_labels)
  gap = eval_util.calculate_gap(predictions, dense_labels)

  log_info = {
      "Training step": global_step,
      "Hit@1": hit_at_one,
      "PERR": perr,
      "GAP": gap,
      "Loss": res["loss"],
      "Global norm": res["global_norm"],
      "Exps/sec": res["examples_per_second"],
  }

  if self.is_chief and global_step % 10 == 0 and self.config.train_dir:
    sv.summary_writer.add_summary(
        utils.MakeSummary("model/Training_Hit@1",
                          hit_at_one), global_step)
    sv.summary_writer.add_summary(
        utils.MakeSummary("model/Training_Perr", perr),
        global_step)
    sv.summary_writer.add_summary(
        utils.MakeSummary("model/Training_GAP", gap),
        global_step)
    sv.summary_writer.add_summary(
        utils.MakeSummary("global_step/Examples/Second",
                          res["examples_per_second"]),
        global_step)
    sv.summary_writer.flush()
  return log_info

def train_loop(self, model_ckpt_path, init_fn=None, start_supervisor_services=True):
  saver = tf.train.Saver(max_to_keep=1000000)

  if len(model_ckpt_path) > 0:
    logging.info("restore from {}".format(model_ckpt_path))
    variables_to_restore = self.model.get_variables_with_ckpt()
    init_fn = slim.assign_from_checkpoint_fn(
        model_ckpt_path,
        variables_to_restore,
        ignore_missing_vars=False,)

  # TODO
  sv = tf.train.Supervisor(logdir=self.config.train_dir,
                           is_chief=self.is_chief,
                           global_step=self.global_step,
                           # TODO
                           save_model_secs=3600,
                           save_summaries_secs=600,
                           saver=saver,
                           init_fn=init_fn)
  sess = sv.prepare_or_wait_for_session(
      self.master,
      start_standard_services=start_supervisor_services,
      config=tf.ConfigProto(log_device_placement=False))

  logging.info("prepared session")
  sv.start_queue_runners(sess)
  logging.info("started queue runners")

  log_fout = open(os.path.join(self.config.train_dir, "train.log"), "w")
  try:
    logging.info("entering training loop")
    while not sv.should_stop():
      batch_start_time = time.time()
      res = sess.run(self.feed_out)
      if self.feed_out1["train_op1"] is not None:
        res = sess.run(self.feed_out1)
      seconds_per_batch = time.time() - batch_start_time
      examples_per_second = res["dense_labels"].shape[0] / seconds_per_batch

      log_info_str = ""
      if res["predictions"] is None:
        log_info = {
          "Training step": res["global_step"],
          "Loss": res["loss"],
          "Global norm": res["global_norm"],
          "Exps/sec": examples_per_second,
        }
      else:
        res["examples_per_second"] = examples_per_second
        log_info = supervised_tasks(self, sv, res)
      for k, v in log_info.iteritems():
        log_info_str += "%s: %.2f;  " % (k, v)
      logging.info(log_info_str)
      log_fout.write(log_info_str+'\n')
      if res["global_step"] % 100 == 0:
        log_fout.flush()

  except tf.errors.OutOfRangeError:
    logging.info("Done training -- epoch limit reached")
  logging.info("exited training loop")
  sv.Stop()

def get_train_op(self, result, label_loss):
  if self.model.optimizer_name == "MomentumOptimizer":
    opt = self.optimizer(self.model.base_learning_rate, 0.9)
  elif self.model.optimizer_name == "RMSPropOptimizer":
    opt = tf.train.RMSPropOptimizer(
        self.model.base_learning_rate,
        decay=0.9,
        momentum=0.9,
        epsilon=1)
  else:
    if self.model.decay_lr:
      learning_rate = tf.train.exponential_decay(
          self.model.base_learning_rate,
          self.global_step * self.batch_size,
          4000000,
          0.95,
          staircase=False
      )
    else:
      learning_rate = self.model.base_learning_rate
    tf.summary.scalar('learning_rate', learning_rate)
    opt = self.optimizer(learning_rate)
  for variable in slim.get_model_variables():
    tf.summary.histogram(variable.op.name, variable)
  tf.summary.scalar("label_loss", label_loss)

  if "regularization_loss" in result.keys():
    reg_loss = result["regularization_loss"]
  else:
    reg_loss = tf.constant(0.0)
  reg_losses = tf.losses.get_regularization_losses()
  if reg_losses:
    reg_loss += tf.add_n(reg_losses)
  if self.config.regularization_penalty != 0:
    tf.summary.scalar("reg_loss", reg_loss)

  update_ops = tf.get_collection(tf.GraphKeys.UPDATE_OPS)
  if "update_ops" in result.keys():
    update_ops += result["update_ops"]

  final_loss = self.config.regularization_penalty * reg_loss + label_loss
  if update_ops:
    with tf.control_dependencies(update_ops):
      barrier = tf.no_op(name="gradient_barrier")
      with tf.control_dependencies([barrier]):
        final_loss = tf.identity(final_loss)

  # Incorporate the L2 weight penalties etc.
  # train_op = optimizer.minimize(final_loss, global_step=global_step)
  params = tf.trainable_variables()
  gradients = tf.gradients(final_loss, params)
  gradients = zip(gradients, params)
  auc_vs, auc_gs = [], []
  other_vs, other_gs = [], []
  for g, v in gradients:
    if v.op.name.startswith("AUCPRLambda"):
      print(v.op.name)
      g = g * -1.
      auc_gs.append(g)
      auc_vs.append(v)
    else:
      other_gs.append(g)
      other_vs.append(v)

  global_norm = tf.global_norm(other_gs)
  if self.model.clip_global_norm > 0:
    other_gs, _ = tf.clip_by_global_norm(other_gs, self.model.clip_global_norm)
  gradients = zip(other_gs, other_vs)
  train_op = opt.apply_gradients(gradients, self.global_step)

  global_norm1 = tf.global_norm(auc_gs)
  auc_gs, _ = tf.clip_by_global_norm(auc_gs, 0.1)
  gradients = zip(auc_gs, auc_vs)
  learning_rate = tf.train.exponential_decay(
        1e-3,
        self.global_step1 * self.batch_size,
        400000,
        0.95,
        staircase=True
    )
  opt1 = tf.train.GradientDescentOptimizer(learning_rate)
  train_op1 = None
  if len(gradients) > 0:
    train_op1 = opt1.apply_gradients(gradients, self.global_step1)

  if self.model.var_moving_average_decay > 0:
    print("moving average")
    variable_averages = tf.train.ExponentialMovingAverage(
      self.model.var_moving_average_decay, self.global_step)
    variables_to_average = (tf.trainable_variables() +
                            tf.moving_average_variables())
    variables_averages_op = variable_averages.apply(variables_to_average)
    train_op = tf.group(train_op, variables_averages_op)

  return train_op, train_op1, label_loss, global_norm

def recover_session(self):
  # Recover session
  saver = None
  latest_checkpoint = tf.train.latest_checkpoint(self.train_dir)
  if self.config.start_new_model:
    logging.info("'start_new_model' flag is set. Removing existing train dir.")
    try:
      gfile.DeleteRecursively(self.train_dir)
    except:
      logging.error(
          "Failed to delete directory " + self.train_dir +
          " when starting a new model. Please delete it manually and" +
          " try again.")
  elif not latest_checkpoint:
    logging.info("No checkpoint file found. Building a new model.")
  else:
    meta_filename = latest_checkpoint + ".meta"
    if not gfile.Exists(meta_filename):
      logging.info("No meta graph file found. Building a new model.")
    else:
      logging.info("Restoring from meta graph file %s", meta_filename)
      saver = tf.train.import_meta_graph(meta_filename)
  return saver

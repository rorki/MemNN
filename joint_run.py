"""Example running MemN2N on a single bAbI task.
Download tasks from facebook.ai/babi """
from __future__ import absolute_import
from __future__ import print_function

from data_utils import load_task, vectorize_data
from sklearn import cross_validation, metrics
from memn2n import MemN2N
from itertools import chain

import tensorflow as tf
import numpy as np
import pandas as pd

from functools import reduce

tf.flags.DEFINE_float("learning_rate", 0.01, "Learning rate for Adam Optimizer.")
tf.flags.DEFINE_float("anneal_rate", 15, "Number of epochs between halving the learnign rate.")
tf.flags.DEFINE_float("anneal_stop_epoch", 60, "Epoch number to end annealed lr schedule.")
tf.flags.DEFINE_float("max_grad_norm", 40.0, "Clip gradients to this norm.")
tf.flags.DEFINE_integer("evaluation_interval", 10, "Evaluate and print results every x epochs")
tf.flags.DEFINE_integer("batch_size", 100, "Batch size for training.")
tf.flags.DEFINE_integer("hops", 3, "Number of hops in the Memory Network.")
tf.flags.DEFINE_integer("epochs", 60, "Number of epochs to train for.")
tf.flags.DEFINE_integer("embedding_size", 30, "Embedding size for embedding matrices.")
tf.flags.DEFINE_integer("memory_size", 50, "Maximum size of memory.")
tf.flags.DEFINE_integer("random_state", None, "Random state.")
tf.flags.DEFINE_string("data_dir", "data/babi-tasks-v1-2/tasks_1-20_v1-2/en-10k/", "Directory containing bAbI tasks")
tf.flags.DEFINE_string("log_dir", "logs", "Directory containing logs")
tf.flags.DEFINE_string("output_file", "scores_10k_memsize_50_embeddingsize_30_with_lstm.csv", "Name of output file for final bAbI accuracy scores.")
FLAGS = tf.flags.FLAGS

if tf.gfile.Exists(FLAGS.log_dir):
    tf.gfile.DeleteRecursively(FLAGS.log_dir)
tf.gfile.MakeDirs(FLAGS.log_dir)

# load all train/test data
ids = range(1, 21)
train, test = [], []
for i in ids:
    tr, te = load_task(FLAGS.data_dir, i)

    train.append(tr)
    test.append(te)
data = list(chain.from_iterable(train + test))

vocab = sorted(reduce(lambda x, y: x | y, (set(list(chain.from_iterable(s)) + q + a) for s, q, a in data)))
word_idx = dict((c, i + 1) for i, c in enumerate(vocab))

max_story_size = max(map(len, (s for s, _, _ in data)))
mean_story_size = int(np.mean([ len(s) for s, _, _ in data ]))
sentence_size = max(map(len, chain.from_iterable(s for s, _, _ in data)))
query_size = max(map(len, (q for _, q, _ in data)))
memory_size = min(FLAGS.memory_size, max_story_size)

# Add time words/indexes
for i in range(memory_size):
    word_idx['time{}'.format(i+1)] = 'time{}'.format(i+1)

vocab_size = len(word_idx) + 1 # +1 for nil word
sentence_size = max(query_size, sentence_size) # for the position
sentence_size += 1  # +1 for time words

print("Longest sentence length", sentence_size)
print("Longest story length", max_story_size)
print("Average story length", mean_story_size)

# train/validation/test sets
trainS = []
valS = []
trainQ = []
valQ = []
trainA = []
valA = []
for task in train:
    S, Q, A = vectorize_data(task, word_idx, sentence_size, memory_size)
    ts, vs, tq, vq, ta, va = cross_validation.train_test_split(S, Q, A, test_size=0.1, random_state=FLAGS.random_state)
    trainS.append(ts)
    trainQ.append(tq)
    trainA.append(ta)
    valS.append(vs)
    valQ.append(vq)
    valA.append(va)

trainS = reduce(lambda a,b : np.vstack((a,b)), (x for x in trainS))
trainQ = reduce(lambda a,b : np.vstack((a,b)), (x for x in trainQ))
trainA = reduce(lambda a,b : np.vstack((a,b)), (x for x in trainA))
valS = reduce(lambda a,b : np.vstack((a,b)), (x for x in valS))
valQ = reduce(lambda a,b : np.vstack((a,b)), (x for x in valQ))
valA = reduce(lambda a,b : np.vstack((a,b)), (x for x in valA))

testS, testQ, testA = vectorize_data(list(chain.from_iterable(test)), word_idx, sentence_size, memory_size)

n_train = trainS.shape[0]
n_val = valS.shape[0]
n_test = testS.shape[0]

print("Training Size", n_train)
print("Validation Size", n_val)
print("Testing Size", n_test)

print(trainS.shape, valS.shape, testS.shape)
print(trainQ.shape, valQ.shape, testQ.shape)
print(trainA.shape, valA.shape, testA.shape)

train_labels = np.argmax(trainA, axis=1)
test_labels = np.argmax(testA, axis=1)
val_labels = np.argmax(valA, axis=1)

tf.set_random_seed(FLAGS.random_state)
batch_size = FLAGS.batch_size

# This avoids feeding 1 task after another, instead each batch has a random sampling of tasks
batches = zip(range(0, n_train-batch_size, batch_size), range(batch_size, n_train, batch_size))
batches = [(start, end) for start,end in batches]

with tf.Session() as sess:
    model = MemN2N(batch_size, vocab_size, sentence_size, memory_size, FLAGS.embedding_size, session=sess,
                   hops=FLAGS.hops, max_grad_norm=FLAGS.max_grad_norm)

    # Merge all the summaries and write them out to /tmp/tensorflow/mnist/logs/mnist_with_summaries (by default)
    merged = tf.summary.merge_all()
    train_writer = tf.summary.FileWriter(FLAGS.log_dir + '/train', sess.graph)
    test_writer = tf.summary.FileWriter(FLAGS.log_dir + '/test')
    val_writer = tf.summary.FileWriter(FLAGS.log_dir + '/val')

    for t in range(1, FLAGS.epochs+1):
        # Stepped learning rate
        if t - 1 <= FLAGS.anneal_stop_epoch:
            anneal = 2.0 ** ((t - 1) // FLAGS.anneal_rate)
        else:
            anneal = 2.0 ** (FLAGS.anneal_stop_epoch // FLAGS.anneal_rate)
        lr = FLAGS.learning_rate / anneal

        np.random.shuffle(batches)
        total_cost = 0.0
        print("epoch " + str(t) + " batches " + str(len(batches)))
        for start, end in batches:
            s = trainS[start:end]
            q = trainQ[start:end]
            a = trainA[start:end]
            cost_t, summary = model.batch_fit(s, q, a, lr, merged)
            total_cost += cost_t
            #print("batch " + str(start) + " " + str(end))

        print("evaluation..")
        if t % FLAGS.evaluation_interval == 0:
            train_accs = []
            print("train accuracy..")
            for start in range(0, n_train, int(n_train / 20)):
                end = start + int(n_train / 20)
                s = trainS[start:end]
                q = trainQ[start:end]
                a = trainA[start:end]
                pred, summary = model.predict(s, q, a, merged)

                acc = metrics.accuracy_score(pred, train_labels[start:end])
                train_accs.append(acc)

            val_accs = []
            print("val accuracy..")
            for start in range(0, n_val, int(n_val / 20)):
                end = start + int(n_val / 20)
                s = valS[start:end]
                q = valQ[start:end]
                a = valA[start:end]
                pred, summary = model.predict(s, q, a, merged)

                acc = metrics.accuracy_score(pred, val_labels[start:end])
                val_accs.append(acc)

            test_accs = []
            print("test accuracy..")
            for start in range(0, n_test, int(n_test / 20)):
                end = start + int(n_test / 20)
                s = testS[start:end]
                q = testQ[start:end]
                a = testA[start:end]
                pred, summary = model.predict(s, q, a, merged)

                acc = metrics.accuracy_score(pred, test_labels[start:end])
                test_accs.append(acc)

            print('-----------------------')
            print('Epoch', t)
            print('Total Cost:', total_cost)
            print()
            i = 1
            for t1, t2, t3 in zip(train_accs, val_accs, test_accs):
                print("Task {}".format(i))
                print("Training Accuracy = {}".format(t1))
                print("Validation Accuracy = {}".format(t2))
                print("Testing Accuracy = {}".format(t3))
                print()
                i += 1
            print('-----------------------')


    print('Writing final results to {}'.format(FLAGS.output_file))
    df = pd.DataFrame({
            'Training Accuracy': train_accs,
            'Validation Accuracy': val_accs,
            'Testing Accuracy': test_accs
    }, index=range(1, 21))
    df.index.name = 'Task'
    df.to_csv(FLAGS.output_file)

    #test_preds, summary = model.predict(testS, testQ, testA, merged)
    #test_writer.add_summary(summary, t)
    #test_acc = metrics.accuracy_score(test_preds, test_labels)

    #print("Testing Accuracy:", test_acc)
    train_writer.close()
    test_writer.close()
    val_writer.close()

import itertools
from multiprocessing import Process, JoinableQueue
import os

import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader
from tqdm import trange
import numpy as np

from data.watermark import Watermark
from net import DFW, pretrain_depth, max_depth
import common.path as path
from test import test_worker


log_filename = './train.log'
pretrain_filename = './pretrain.pt'


class DFWTrain(DFW):
    def __init__(self, args, data):
        super().__init__(args, data)

        self.enc_scale, self.dec_scale = args.enc_scale, args.dec_scale
        self.optimizer = torch.optim.Adam(itertools.chain(self.encoder.parameters(), self.decoder.parameters()))

    def pre_optimize(self, msg):
        self.train()

        watermark = self.encoder(msg)
        decoded_msg = self.decoder(watermark)

        except_batch = list(range(1, watermark.dim()))  # can be fc or conv
        enc_loss = torch.norm(watermark, p=2, dim=except_batch).mean()
        loss = F.binary_cross_entropy_with_logits(decoded_msg, msg)

        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()

        return {'loss': loss.item(),}


    def optimize(self, img, msg, enc_scale, limit):
        self.train()

        watermark = self.encoder(msg)
        encoded_img = (img + watermark).clamp(-limit, limit)
        noised_img, _ = self.noiser([encoded_img, img])
        decoded_msg = self.decoder(noised_img)

        enc_loss = torch.norm(watermark, p=2, dim=(1, 2, 3)).mean()
        dec_loss = F.binary_cross_entropy_with_logits(decoded_msg, msg)
        loss = enc_scale*enc_loss + self.dec_scale*dec_loss

        self.optimizer.zero_grad()
        loss.backward()
        self.optimizer.step()

        return {
            'loss': loss.item(),
            'enc_loss': enc_loss.item(),
            'dec_loss': dec_loss.item(),
        }
    
    def evaluate(self, img, msg, enc_scale, limit):
        self.eval()
        with torch.no_grad():
            watermark = self.encoder(msg)
            encoded_img = (img + watermark).clamp(-limit, limit)
            noised_img, _ = self.noiser([encoded_img, img])
            decoded_msg = self.decoder(noised_img)

            #diff between original image and watermarked image must be minimized
            enc_loss = torch.norm(img-encoded_img, p=2, dim=(1, 2, 3)).mean()
            #acc of the decoder must be maximized
            dec_loss = F.binary_cross_entropy_with_logits(decoded_msg, msg)
            loss = self.enc_scale*enc_loss + self.dec_scale*dec_loss

            return {
                'loss': loss.item(),
                'enc_loss': enc_loss.item(),
                'dec_loss': dec_loss.item(),
                }


def start_test_process(args):
    queue = JoinableQueue()
    test_process = Process(target=test_worker, args=(args, queue))
    test_process.start()

    return test_process, queue


def pretrain(args):
    dataset = Watermark(args.img_size, args.msg_l, train=True, dev=False)
    net = DFWTrain(args, dataset).to(args.device)

    print('Pre-training Start')
    depth = 1
    while depth <= pretrain_depth:
        net.set_depth(depth)
        msg = dataset.msg_dist.sample((args.batch_size, )).to(args.device)
        stats = net.pre_optimize(msg)
        if stats['loss'] < 0.05:
            print(f"Grown: {depth}/{pretrain_depth} | loss: {stats['loss']}")
            if depth == 2:
                depth += 2
            else:
                depth += 1
    torch.save(net.state_dict(), pretrain_filename)
    print('Pre-trained Weight Saved')



def train(args):
    test_process, queue = start_test_process(args)
    log_file = open(log_filename, 'w+', buffering=1)

    train_set = Watermark(args.img_size, args.msg_l, train=True,  dev=False)
    dev_set = Watermark(args.img_size, args.msg_l, train=False,  dev=True)
    train_loader = DataLoader(dataset=train_set, batch_size=args.batch_size, shuffle=True)
    dev_loader = DataLoader(dataset=dev_set, batch_size=args.batch_size, shuffle=True)
    net = DFWTrain(args, train_set).to(args.device)

    if not os.path.exists(pretrain_filename):
        raise FileNotFoundError('Pre-trained weight not found')
    net.load_state_dict(torch.load(pretrain_filename))
    print('Pre-trained Weight Loaded')

    net.set_depth(max_depth)
    valid_loss_min = np.Inf 
    with trange(args.epochs, unit='epoch') as tqdm_bar:
        
        for epoch_i in tqdm_bar:
            ######################    
            # train the model #
            ######################
            enc_scale = args.enc_scale * min(1, epoch_i / args.annealing_epochs)
            limit = max(1, 5 - 4 * epoch_i / args.annealing_epochs)

            for batch_i, (img, msg) in enumerate(train_loader):
                img, msg = img.to(args.device), msg.to(args.device)
                stats = net.optimize(img, msg, enc_scale, limit)
                tqdm_bar.set_postfix(**stats)

            if epoch_i % args.save_freq == 0:
                log_file.write("Epoch {} | {}\n".format(epoch_i,  " ".join([f"{k}: {v:.3f}" for k, v in stats.items()])))

            ######################    
            # validate the model #
            ######################
            list_valid_loss = []
            for dev_batch, (img, msg) in enumerate(dev_loader):
                img, msg = img.to(args.device), msg.to(args.device)
                stats = net.evaluate(img, msg, enc_scale, limit)
                list_valid_loss.append(stats['loss'])
            valid_loss = np.mean(list_valid_loss)
            if valid_loss <= valid_loss_min:
                print('Validation loss decreased ({:.6f} --> {:.6f}).  Saving model ...\n'.format(valid_loss_min,valid_loss))
                log_file.write('Validation loss decreased ({:.6f} --> {:.6f}).  Saving model ...\n'.format(valid_loss_min, valid_loss))
                torch.save(net.state_dict(), path.save_path)
                valid_loss_min = valid_loss
    log_file.close()
    queue.join()
    test_process.terminate()

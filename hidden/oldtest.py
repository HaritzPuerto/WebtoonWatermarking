import numpy as np
import torch
from torch.utils.data import DataLoader

from data.watermark import Watermark
from net import Hidden
import common.path as path


log_filename = './test.log'


class HiddenTest(Hidden):
    def stats(self, img, msg):
        self.eval()

        encoded_img = self.encoder(img, msg)
        noised_img = self.noiser(encoded_img)
        decoded_msg = self.decoder(noised_img)
        pred_msg = (torch.sigmoid(decoded_msg) > 0.5).int()
        D_loss = self.D_loss(img, encoded_img)

        enc_loss = self.enc_loss(encoded_img, img)
        dec_loss = self.dec_loss(decoded_msg, msg)
        adv_loss = self.adv_loss(encoded_img)
        #G_loss = self.G_loss(enc_loss, dec_loss, adv_loss)
        correct = (pred_msg == msg).sum(1)
        print(correct)
        return {
            'D_loss': D_loss,
            'enc_loss': enc_loss,
            'dec_loss': dec_loss,
            'adv_loss': adv_loss
        }


def test_worker(args, queue):
    log_file = open(log_filename, 'w+', buffering=1)

    dataset = Watermark(args.img_size, train=False, dev=False)
    net = HiddenTest(args, dataset).to(args.test_device)
    loader = DataLoader(dataset=dataset, batch_size=args.batch_size, shuffle=False)
    msg_dist = torch.distributions.Bernoulli(probs=0.5*torch.ones(args.msg_l))
    while True:
        epoch_i, state_dict = queue.get()
        net.load_state_dict(state_dict)

        stats = {
            'D_loss': 0,
            'enc_loss': 0,
            'dec_loss': 0,
            'adv_loss': 0
        }

        with torch.no_grad():
            for img in loader:
                msg = msg_dist.sample([img.shape[0]])
                img, msg = img.to(args.test_device), msg.to(args.test_device)
                batch_stats = net.stats(img, msg)
                for k in stats:
                    stats[k] += len(img) * batch_stats[k]

        for k in stats:
            stats[k] = stats[k] / len(dataset)

        log_file.write("Epoch {} | {}\n".format(epoch_i,  " ".join([f"{k}: {v:.3f}"for k, v in stats.items()])))
        queue.task_done()

def test(args):
    dataset = Watermark(args.img_size, train=False, dev=False)
    net = HiddenTest(args, dataset).to(args.test_device)
    loader = DataLoader(dataset=dataset, batch_size=args.batch_size, shuffle=False)
    msg_dist = torch.distributions.Bernoulli(probs=0.5*torch.ones(args.msg_l))
    net.load_state_dict(torch.load(path.save_path))

    stats = {
        'D_loss': 0,
        'enc_loss': 0,
        'dec_loss': 0,
        'adv_loss': 0
    }

    with torch.no_grad():
        for img in loader:
            msg = msg_dist.sample([img.shape[0]])
            img, msg = img.to(args.test_device), msg.to(args.test_device)
            batch_stats = net.stats(img, msg)
            for k in stats:
                stats[k] += len(img) * batch_stats[k]

    for k in stats:
        stats[k] = stats[k] / len(dataset)

    print(" ".join([f"{k}: {v:.3f}"for k, v in stats.items()]))


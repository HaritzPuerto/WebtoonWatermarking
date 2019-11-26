import os

import matplotlib; matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import torch
import torch.nn.functional as F
from torch.utils.data import DataLoader

from data.watermark import Watermark, denormalize
from net import DFW
import common.path as path
import json


def save_img(args):
    save_dir = './examples_' + args.noise_type
    if not os.path.exists(save_dir):
        os.mkdir(save_dir)
    
    dataset = Watermark(args.img_size, train=False, dev=False)
    msg_dist = torch.distributions.Bernoulli(probs=0.5*torch.ones(args.msg_l))
    loader = DataLoader(dataset=dataset, batch_size=args.n_imgs, shuffle=False)
    
    net = DFW(args, dataset).to(args.device)
    net.encoder.depth=9;net.decoder.depth=9   
    net.load_state_dict(torch.load(path.save_path))

    with torch.no_grad():
        img = next(iter(loader))
        msg = msg_dist.sample([args.n_imgs])
        img, msg = img.to(args.device), msg.to(args.device)
        
        net.eval()
        watermark = net.encoder(msg)
        encoded_img = (img + watermark).clamp(-1, 1)
        noised_img = net.noiser(encoded_img)
        decoded_msg = net.decoder(noised_img)
        
            

    convert = lambda img: np.moveaxis(denormalize(img).cpu().numpy(), [1, 2, 3], [3, 1, 2])
    img = convert(img)
    watermark = convert(watermark / abs(watermark).max())
    encoded_img = convert(encoded_img)
    noised_img = convert(noised_img)
    msg = msg.cpu().numpy()
    decoded_msg = (decoded_msg>0.5).float().cpu().numpy()
    
    dict_output_info = {}
    for i in range(args.n_imgs):
        fig = plt.figure()
        gridspec = fig.add_gridspec(ncols=6, nrows=1, width_ratios=[2, 2, 2, 2, 1, 1])
        axes = [fig.add_subplot(gridspec[0, i]) for i in range(6)]
        ax1, ax2, ax3, ax4, ax5, ax6 = axes
        
        for ax in axes:
            ax.set_xticks([])
            ax.set_yticks([])
        
        ax1.set_title('Original\nImage')
        ax1.imshow(img[i])

        ax2.set_title('Watermark\n(Normalized)')
        ax2.imshow(watermark[i])

        ax3.set_title('Encoded\nImage')
        ax3.imshow(encoded_img[i])

        ax4.set_title('Noised\nImage')
        ax4.imshow(noised_img[i])

        ax5.set_title('Original\nMsg')
        ax5.imshow(msg[i][:, None], cmap='gray', aspect=2/31)
        
        ax6.set_title('Decoded\nMsg')
        ax6.imshow(decoded_msg[i][:, None], cmap='gray', aspect=2/31)
        
        fig.tight_layout()
        fig.savefig(os.path.join(save_dir, f'{i}.png'), bbox_inches='tight')

        dict_output_info[i] = sum(abs(decoded_msg[i]-msg[i])) #number of errors
    print(dict_output_info)
    with open("qualitative_eval.json", "w+") as f:
        json.dump(dict_output_info, f)
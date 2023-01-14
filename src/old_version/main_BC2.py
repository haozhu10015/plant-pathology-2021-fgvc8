import os
import gc
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader
from torchvision import transforms
from intput.src.dataset import FGVC8_Dataset
from utils.train import ModelTrainer, plot_line
from utils.label_smoothing import LabelSmoothingCrossEntropy
from models.effnetv2 import effnetv2_s
from models.resnet import *
from models.CBAM.model_resnet import *
from models.resnext_wsl import *
from datetime import datetime
import numpy as np
import argparse

src_path = os.path.dirname(os.path.abspath(__file__))
input_path = os.path.dirname(src_path)
root_path = os.path.dirname(input_path)
output_path = os.path.join(root_path, 'working')

parser = argparse.ArgumentParser()
parser.add_argument('--kaggle', action='store_true', help='if the script is run on kaggle')
parser.add_argument('--batch_size', type=int, default=48, help='size of each image batch')
parser.add_argument('--learning_rate', type=float, default=0.01, help='learning rate')
parser.add_argument('--max_epoch', type=int, default=50, help='number of epochs')
parser.add_argument('--model', type=str, default='effinetv2',
                    help='which model to use: effinetv2'
                         'resnext50_32x4d'
                         'wide_resnet101_2'
                         'cbam_resnet101'
                         'cbam_resnet50'
                         'resnext101_32x16d'
                         'resnet18'
                         '')
parser.add_argument('--pretrained', action='store_true', help='whether to use pretrained model')
parser.add_argument('--criterion', type=str, default='CR', help='which criterion to use: CR, LSCR')
parser.add_argument('--optimizer', type=str, default='SGD', help='optimizer： SGD, Adam')
parser.add_argument('--num_workers', type=int, default=4, help='number of cpu threads to use during batch generation')
parser.add_argument('--chkpoint', type=str, default=None, help='checkpoint')
parser.add_argument('--print_acc_iter', type=int, default=10, help='print accuracy per n iteration')
opt = parser.parse_args()

kaggle_prefix = 'fgvc8-roger10015-img-aug-bc2' if opt.kaggle else ''
device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

if __name__ == '__main__':
    now_time = datetime.now()
    time_str = datetime.strftime(now_time, '%Y-%m-%d_%H-%M')
    log_dir = os.path.join(output_path, 'log', time_str)
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    log_file = os.path.join(log_dir, 'log.txt')
    if os.path.exists(log_file):
        os.remove(log_file)

    img_cls = {'simple_disease': 0, 'complex_disease': 1}
    num_cls = len(img_cls)

    data_dir = os.path.join(input_path, kaggle_prefix, 'img_aug')

    norm_mean = [0.485, 0.456, 0.406]
    norm_std = [0.229, 0.224, 0.225]

    train_transform = transforms.Compose([
        transforms.ToPILImage(),
        transforms.Resize((256, 256)),
        transforms.RandomCrop((224, 224)),
        transforms.ToTensor(),
        transforms.Normalize(norm_mean, norm_std)
    ])
    val_transform = transforms.Compose([
        transforms.ToPILImage(),
        transforms.Resize((256, 256)),
        transforms.CenterCrop((224, 224)),
        transforms.ToTensor(),
        transforms.Normalize(norm_mean, norm_std)
    ])

    train_txt_path = os.path.join(output_path, 'train.txt')
    val_txt_path = os.path.join(output_path, 'val.txt')

    train_dataset = FGVC8_Dataset(train_txt_path, train_transform)
    val_dataset = FGVC8_Dataset(val_txt_path, val_transform)

    train_dl = DataLoader(dataset=train_dataset, batch_size=opt.batch_size, shuffle=True, num_workers=opt.num_workers)
    val_dl = DataLoader(dataset=val_dataset, batch_size=int(opt.batch_size / 4), num_workers=opt.num_workers)

    if opt.model == 'effinetv2':
        model = effnetv2_s(num_classes=num_cls)
    elif opt.model == 'resnext50_32x4d':
        if opt.pretrained:
            model = resnext50_32x4d(pretrained=opt.pretrained)
            model.fc = nn.Linear(model.fc.in_features, num_cls)
        else:
            model = resnext50_32x4d(num_classes=num_cls, pretrained=opt.pretrained)
    elif opt.model == 'resnet18':
        if opt.pretrained:
            model = resnet18(pretrained=opt.pretrained)
            model.fc = nn.Linear(model.fc.in_features, num_cls)
        else:
            model = resnext50_32x4d(num_classes=num_cls, pretrained=opt.pretrained)
    elif opt.model == 'wide_resnet101_2':
        if opt.pretrained:
            model = wide_resnet101_2(pretrained=opt.pretrained)
            model.fc = nn.Linear(model.fc.in_features, num_cls)
        else:
            model = wide_resnet101_2(num_classes=num_cls, pretrained=opt.pretrained)
    elif opt.model == 'cbam_resnet101':
        model = ResidualNet('ImageNet', 101, num_cls, 'CBAM')
    elif opt.model == 'cbam_resnet50':
        if opt.pretrained:
            model = ResidualNet('ImageNet', 50, 1000, 'CBAM')
            model = nn.DataParallel(model)
            model.load_state_dict(torch.load(os.path.join(src_path, 'RESNET50_CBAM_new_name_wrap.pth'), map_location=device)['state_dict'])
            model.module.fc = nn.Linear(model.module.fc.in_features, num_cls)
        else:
            model = ResidualNet('ImageNet', 50, num_cls, 'CBAM')
    elif opt.model == 'resnext101_32x16d':
        if opt.pretrained:
            model = resnext101_32x16d_wsl(pretrained=opt.pretrained)
            model.fc = nn.Linear(model.fc.in_features, num_cls)
        else:
            model = resnext101_32x16d_wsl(num_classes=num_cls, pretrained=opt.pretrained)

    if opt.optimizer == 'SGD':
        optimizer = optim.SGD(model.parameters(), lr=opt.learning_rate, momentum=0.9, weight_decay=1e-4)
    elif opt.optimizer == 'Adam':
        optimizer = optim.Adam(model.parameters(), lr=opt.learning_rate)

    scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, mode='min', factor=0.6, patience=5)

    if opt.chkpoint is not None and not opt.pretrained:
        checkpoint = torch.load(opt.chkpoint, map_location=device)
        model.load_state_dict(checkpoint['model_state_dict'])
        print('~~~Model checkpoint loaded.~~~')
        if log_file is not None:
            with open(log_file, 'a') as f:
                print('~~~Model checkpoint loaded.~~~', file=f)
        if 'optimizer_state_dict' in checkpoint:
            optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
            for k, v in optimizer.state.items():  # key is Parameter, val is a dict {key='momentum_buffer':tensor(...)}
                if 'momentum_buffer' not in v:
                    continue
                optimizer.state[k]['momentum_buffer'] = optimizer.state[k]['momentum_buffer'].to(device)
            print('~~~Optimizer checkpoint loaded.~~~')
            if log_file is not None:
                with open(log_file, 'a') as f:
                    print('~~~Optimizer checkpoint loaded.~~~', file=f)
    model.to(device)

    if opt.criterion == 'CR':
        criterion = nn.CrossEntropyLoss()
    elif opt.criterion == 'LSCR':
        criterion = LabelSmoothingCrossEntropy()

    criterion = criterion.to(device)

    loss_rec = {'train': [], 'val': []}
    acc_rec = {'train': [], 'val': []}
    if opt.chkpoint is not None:
        best_acc = checkpoint['best_acc']
        best_epoch = 0
        del checkpoint
        gc.collect()
    else:
        best_acc, best_epoch = 0, 0

    start_epoch = -1
    for epoch in range(start_epoch + 1, opt.max_epoch):
        loss_train, acc_train, mat_train = ModelTrainer().train(train_dl, model, criterion, optimizer, device, epoch,
                                                                opt.max_epoch, num_cls, opt.print_acc_iter, log_file)
        loss_val, acc_val, mat_val = ModelTrainer().valid(val_dl, model, criterion, device, num_cls)

        scheduler.step(loss_val)

        print(
            "Epoch[{:0>3}/{:0>3}] Train Acc: {:.2%} Valid Acc:{:.2%} Train loss:{:.4f} Valid loss:{:.4f} LR:{}".format(
                epoch + 1, opt.max_epoch, acc_train, acc_val, loss_train, loss_val, optimizer.param_groups[0]["lr"]))
        if log_file is not None:
            with open(log_file, 'a') as f:
                print(
                    "Epoch[{:0>3}/{:0>3}] Train Acc: {:.2%} Valid Acc:{:.2%} Train loss:{:.4f} Valid loss:{:.4f} LR:{}".format(
                        epoch + 1, opt.max_epoch, acc_train, acc_val, loss_train, loss_val,
                        optimizer.param_groups[0]["lr"]), file=f)

        loss_rec["train"].append(loss_train), loss_rec["val"].append(loss_val)
        acc_rec["train"].append(acc_train), acc_rec["val"].append(acc_val)

        plt_x = np.arange(1, epoch + 2)
        plot_line(plt_x, loss_rec["train"], plt_x, loss_rec["val"], mode="loss", out_dir=log_dir)
        plot_line(plt_x, acc_rec["train"], plt_x, acc_rec["val"], mode="acc", out_dir=log_dir)

        if best_acc < acc_val:
            best_acc = acc_val
            best_epoch = epoch + 1

            checkpoint = {
                'model_state_dict': model.state_dict(),
                'optimizer_state_dict': optimizer.state_dict(),
                'epoch': epoch,
                'best_acc': best_acc,
                'best_epoch': best_epoch
            }
            checkpoint_path = os.path.join(log_dir, 'checkpoint_best.pkl')
            torch.save(checkpoint, checkpoint_path)

            print('~~~ Best Model Saved. Best Acc: {}, Best Epoch: {}. ~~~'.format(best_acc, best_epoch))
            if log_file is not None:
                with open(log_file, 'a') as f:
                    print('~~~ Best Model Saved. Best Acc: {}, Best Epoch: {}. ~~~'.format(best_acc, best_epoch),
                          file=f)

    print(" done ~~~~ {}, best acc: {} in :{} epochs. ".format(datetime.strftime(datetime.now(), '%Y-%m-%d_%H-%M'),
                                                               best_acc, best_epoch))
    if log_file is not None:
        with open(log_file, 'a') as f:
            print(" done ~~~~ {}, best acc: {} in :{} epochs. ".format(
                datetime.strftime(datetime.now(), '%Y-%m-%d_%H-%M'),
                best_acc, best_epoch), file=f)

    now_time = datetime.now()
    time_str = datetime.strftime(now_time, '%Y-%m-%d_%H-%M')
    print(time_str)
    if log_file is not None:
        with open(log_file, 'a') as f:
            print(time_str, file=f)

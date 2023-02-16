import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

from torchvision import datasets, transforms
from torch.utils.data import DataLoader, Dataset, random_split
import cv2

def dice_coeff_t(pred, target):
    smooth = 1.
    num = pred.size(0)
    m1 = pred.view(num, -1)  # Flatten
    m2 = target.view(num, -1)  # Flatten
    intersection = (m1 * m2).sum()

    return (2. * intersection + smooth) / (m1.sum() + m2.sum() + smooth)

def mIoU(pred, target, num_classes):
    iou = np.ones(num_classes)
    target = target.numpy()
    for c in range(1, num_classes):
        p = (pred == c)
        t = (target == c)
        smooth = 0.001
        num = pred.size(0)
        m1 = pred.view(num, -1)  # Flatten
        m2 = target.view(num, -1)  # Flatten
        inter = (m1 * m2).sum()
        union = m1.sum() + m2.sum() - inter
        iou[c] = (inter + 0.001) / (union + 0.001)
    
    miou = np.mean(iou)
    return miou, iou

def dice_coeff(pred, target):
    smooth = 0.001
    num = pred.size(0)
    m1 = pred.view(num, -1)  # Flatten
    m2 = target.view(num, -1)  # Flatten
    intersection = (m1 * m2).sum()
    
    dice = (2. * intersection + smooth) / (m1.sum() + m2.sum() + smooth)
    iou = (intersection + smooth) / (m1.sum() + m2.sum() - intersection + smooth)
    return dice, iou

def class_dice(pred, target, epsilon = 1e-6):
    num_classes = len(torch.unique(target))
    pred_class = torch.argmax(pred, dim = 1)
    #pred_class = torch.argmax(pred.squeeze(), dim=1).detach().cpu().numpy()
    dice = torch.ones(num_classes-1)
    dscore = torch.ones(num_classes-1)
    iou_score = torch.ones(num_classes-1)
    for c in range(1, num_classes):
        p = (pred_class == c)
        t = (target == c)
        #print('p shape: ', p.shape)
        #print('t shape: ', t.shape)
        dc, iou = dice_coeff(p, t)
        #print('dc done')
        dice[c-1] = 1 - dc
        dscore[c-1] = dc
        iou_score[c-1] = iou
        #print('appended')
        dl = torch.sum(dice)
        ds = torch.mean(dscore)
        ious = torch.mean(iou_score)
        
    return dl.float(), ds, ious

def weights(pred, target, epsilon = 1e-6):
    num_classes = 2
    pred_class = torch.argmax(pred, dim = 1)
    #pred_class = torch.argmax(pred.squeeze(), dim=1).detach().cpu().numpy()
    dice = np.ones(num_classes)
    tot = 0
    for c in range(num_classes):
        t = (target == c).sum()
        tot = tot + t
        #print(t.shape)
        dice[c] = t

    dice = dice/dice.sum()
    dice = 1 - dice
    #print('Dice: ', dice)
    return torch.from_numpy(dice).float()

def l2_loss(input_, t_seg, target, weight):

    rand_1 = torch.rand_like(target) < weight[1]
    rand_1 = rand_1.float()
    ones = torch.ones_like(target)
    #print('l2 shapes: ', target.shape, ' ', input_.shape)
    drop = torch.where(rand_1 == 1., input_, ones)
    target = target * drop
    
    #print('t seg shape: ', t_seg.shape)
    t_seg_in = torch.sum(t_seg[:, :-1], 1)[:, None] # ===============
    t_seg_out = torch.ones_like(t_seg_in) - t_seg_in

    loss = (input_ - target) ** 2
    #print('shapesss: ', loss.shape, ' ', t_seg_in.shape)
    loss = (loss * t_seg_in * weight[0])# + (loss * t_seg_out)

    return torch.mean(loss)

def Segloss(pred, target, weight):
    pred = torch.argmax(pred, dim = 1)
    weight = torch.squeeze(weight, dim = 1)
    #print('pred shape: ', pred.shape)
    #print('target shape: ', target.shape)
    #rint('weights shape: ', weight.shape)
    FP = torch.sum(weight * (1 - target) * pred)
    FN = torch.sum(weight * (1 - pred) * target)
    return FP, FN

class FocalTwerskyLoss(nn.Module):
    def __init__(self, num_classes, alpha, beta, phi):
        super(FocalTwerskyLoss, self).__init__()
        self.num_classes = num_classes
        self.alpha = alpha
        self.beta = beta
        self.phi = phi
        
    def class_dice(self, pred, target, epsilon = 1e-6):
        pred_class = torch.argmax(pred, dim = 1)
        #pred_class = torch.argmax(pred.squeeze(), dim=1).detach().cpu().numpy()
        dice = np.ones(2)
        for c in range(2):
            p = (pred_class == c)
            t = (target == c)
            inter = (p * t).sum().float()
            union = p.sum() + t.sum() + epsilon
            d = 2 * inter / union
            dice[c] = 1 - d
        
        return torch.from_numpy(dice).float()

    def tversky_loss(self, pred, target, weights):
        #pred = torch.argmax(pred, dim = 1)
        #weights = torch.squeeze(weights, dim = 1)
        target_oh = torch.eye(self.num_classes)[target.squeeze(1)]
        target_oh = target_oh.permute(0, 3, 1, 2).float()
        m = nn.Softmax(dim=1)
        probs = m(pred)
        target_oh = target_oh.type(pred.type())
        dims = (0,) + tuple(range(2, target.ndimension()))
        inter = torch.sum(probs * target_oh, dims)
        fpb = weights*(probs * (1 - target_oh))
        fnb = weights*((1 - probs) * target_oh)
        #print('weights shape: ', weights.shape)
        #print('fpb and fnb shapes: ', fpb.shape, fnb.shape)
        fp = torch.sum(fpb, dims)
        fn = torch.sum(fnb, dims)
        t = (inter / (inter + (self.alpha * fn) + (self.beta * fp))).mean()
        
        return t
    
    
    def forward(self, mask, target, weights, cross_entropy_weight = 0.5, tversky_weight = 0.5):
        
        #edge_loss = nn.CrossEntropyLoss()#weight = self.class_dice(mask, edge_target).cuda())
        #eloss = edge_loss(edges, edge_target)

        loss = self.tversky_loss(mask, target, weights)
        focal_loss = (1 - loss)**self.phi

        total_loss = focal_loss# + eloss
        
        return total_loss

def comined_loss(pred, target, weights, epoch):

    loss = nn.CrossEntropyLoss()#weight = weights(pred, target).cuda())
    ce = loss(pred, target)
    
    ftl_func = FocalTwerskyLoss(2, 0.2, 0.8, 3/5)
    ftl = ftl_func(pred, target, weights)
    
    #FP, FN = Segloss(pred, target, weights)
    #s_loss = lamda * FP + FN
    #print('before dc')
    dl, dsc, ious = class_dice(pred, target)
    #if epoch > 15:
        #loss = ftl + (1-dsc)
    #else:
    loss = ce + ftl + (1-ious)**2
    
    return loss, dsc, ious


def comined_loss_aff(pred, aff_pred, target, aff_target):
    
    aff_calc_weight = [1.5, 0.5]
    
    loss = nn.CrossEntropyLoss()#weight = weights(pred, target).cuda())
    t_squeezed = torch.squeeze(target, dim = 1)
    ce = loss(pred, t_squeezed)
    
    aff_loss = l2_loss(aff_pred, target.float(),
                       aff_target[:, 0, :, :aff_pred.shape[2], :aff_pred.shape[3]],
                                  aff_calc_weight)
    #print('before dc')
    dl, dsc, ious = class_dice(pred, target)
    
    loss = ce + aff_loss# + dl
    return loss, dsc, ious


# ================================================ loaders ========================================

class DataPrep(Dataset):
    def __init__(self, path):
        self.data = np.load(path, allow_pickle = True)
        self.len = len(self.data) - 20
        self.data = self.data[0:self.len]
        self.labels = 2
        self.aff_r = 5
        self.img_size = 259
        self.mean = [0.477, 0.451, 0.411]
        self.std = [0.284, 0.280, 0.292]
        
        self.transform_img = transforms.Compose([transforms.ToTensor(),
                                  transforms.Normalize(mean = self.mean,
                                                       std = self.std)])
        self.transform = transforms.ToTensor()
        
    def __len__(self):
        return len(self.data)
    
    def __getitem__(self, index):
        img, img_t, img_t_aff, _ = self.data[index]
        
        img = cv2.resize(img, (259,259)) # 224 for swin
        img_t = cv2.resize(img_t, (259, 259), interpolation = cv2.INTER_NEAREST)
        img_t_aff = cv2.resize(img_t_aff, (259, 259), interpolation = cv2.INTER_NEAREST)
        #print('loader 1 shape: ', img_t_aff.shape)
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        img = (img/np.max(img))
        
        '''clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(16,16))
        img_proc = clahe.apply(img.astype(np.uint8))
        
        img = np.expand_dims(img, axis = 2)
        img_proc = np.expand_dims(img_proc, axis = 2)
        img = np.concatenate((img, img_proc), axis = 2)'''
        
        out_data = self.transform(img)
        out_t = self.transform(img_t)
        img_t_aff = self.transform(img_t_aff)
        #print('loader 1 shape: ', img_t_aff.shape)
        return out_data, out_t, img_t_aff

class Test_DataPrep(Dataset):
    def __init__(self, path):
        self.data = np.load(path, allow_pickle = True)
        self.len = len(self.data) - 20
        self.data = self.data[self.len:len(self.data)]
        self.labels = 2
        self.aff_r = 5
        self.img_size = 259
        self.mean = [0.477, 0.451, 0.411]
        self.std = [0.284, 0.280, 0.292]
        
        self.transform_img = transforms.Compose([transforms.ToTensor(),
                                  transforms.Normalize(mean = self.mean,
                                                       std = self.std)])
        self.transform = transforms.ToTensor()
        
    def __len__(self):
        return len(self.data)
    
    def __getitem__(self, index):
        img, img_t, img_t_aff, _ = self.data[index]
        
        img = cv2.resize(img, (259,259))
        img_t = cv2.resize(img_t, (259, 259), interpolation = cv2.INTER_NEAREST)
        img_t_aff = cv2.resize(img_t_aff, (259, 259), interpolation = cv2.INTER_NEAREST)
        
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        img = (img/np.max(img))
        
        '''clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(16,16))
        img_proc = clahe.apply(img.astype(np.uint8))
        
        img = np.expand_dims(img, axis = 2)
        img_proc = np.expand_dims(img_proc, axis = 2)
        img = np.concatenate((img, img_proc), axis = 2)'''
        
        out_data = self.transform(img)
        out_t = self.transform(img_t)
        img_t_aff = self.transform(img_t_aff)
        
        return out_data, out_t, img_t_aff

# ================================= affinity loaders ======================================

class DataPrep_affinity(Dataset):
    def __init__(self, path):
        self.data = np.load(path, allow_pickle = True)
        self.len = len(self.data) - 20
        self.data = self.data[0:self.len]
        self.labels = 2
        self.aff_r = 5
        self.img_size = 128
        self.mean = [0.477, 0.451, 0.411]
        self.std = [0.284, 0.280, 0.292]
        
        self.transform_img = transforms.Compose([transforms.ToTensor(),
                                  transforms.Normalize(mean = self.mean,
                                                       std = self.std)])
        self.transform = transforms.ToTensor()
        
    def __len__(self):
        return len(self.data)
    
    def __getitem__(self, index):
        img, img_t, img_t_aff, weights, _ = self.data[index]
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        img = (img/np.max(img))*255
        
        img = cv2.resize(img, (256,256))
        img_t = cv2.resize(img_t, (256, 256), interpolation = cv2.INTER_NEAREST)
        img_t_aff = cv2.resize(img_t_aff, (256, 256), interpolation = cv2.INTER_NEAREST)

        
        out_t_aff = torch.zeros((self.aff_r, self.aff_r**2,
                                 self.img_size, self.img_size))
        
        out_data = img#np.transpose(img, (2, 0, 1))
        out_t = img_t
        
        out_data = self.transform(out_data)
        out_t = self.transform(out_t)

        for mul in range(5):
            img_t_aff_mul = img_t_aff[0:self.img_size:2**mul,
                                      0:self.img_size:2**mul]
            img_size = self.img_size // (2**mul)

            # 上下左右2pixelずつ拡大
            img_t_aff_mul_2_pix = np.zeros((img_size
                                            + (self.aff_r//2)*2,
                                            img_size
                                            + (self.aff_r//2)*2, 3))
            img_t_aff_mul_2_pix[self.aff_r//2:
                                img_size+self.aff_r//2,
                                self.aff_r//2:
                                img_size+self.aff_r//2] \
                = img_t_aff_mul

            img_t_aff_compare = np.zeros((self.aff_r**2,
                                         img_size, img_size, 3))
            # 1pixelずつずらす
            for i in range(self.aff_r):
                for j in range(self.aff_r):
                    img_t_aff_compare[i*self.aff_r+j] \
                        = img_t_aff_mul_2_pix[i:i+img_size,
                                              j:j+img_size]

            # 同じ色ならAffinity=1(同じ物体)/同じ色でなければAffinity=0(別の物体)
            aff_data = np.where((img_t_aff_compare[:, :, :, 0]
                                 == img_t_aff_mul[:, :, 0])
                                & (img_t_aff_compare[:, :, :, 1]
                                   == img_t_aff_mul[:, :, 1])
                                & (img_t_aff_compare[:, :, :, 2]
                                   == img_t_aff_mul[:, :, 2]), 1, 0)
            aff_data = self.transform(aff_data.transpose(1, 2, 0))
            out_t_aff[mul, :, 0:img_size, 0:img_size] = aff_data
            
            #print('out shape: ', out_data.shape)
            #print('target shape: ', out_t.shape)
            #print('aff shape: ', out_t_aff.shape)
        return out_data, out_t, out_t_aff

class Test_DataPrep_affinity(Dataset):
    def __init__(self, path):
        self.data = np.load(path, allow_pickle = True)
        self.len = len(self.data) - 20
        self.data = self.data[self.len:len(self.data)]
        self.labels = 2
        self.aff_r = 5
        self.img_size = 128
        self.mean = [0.477, 0.451, 0.411]
        self.std = [0.284, 0.280, 0.292]
        
        self.transform_img = transforms.Compose([transforms.ToTensor(),
                                  transforms.Normalize(mean = self.mean,
                                                       std = self.std)])
        self.transform = transforms.ToTensor()
        
    def __len__(self):
        return len(self.data)
    
    def __getitem__(self, index):
        img, img_t, img_t_aff, weights, _ = self.data[index]
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        img = (img/np.max(img))*255
        
        img = cv2.resize(img, (256,256))
        img_t = cv2.resize(img_t, (256, 256), interpolation = cv2.INTER_NEAREST)
        img_t_aff = cv2.resize(img_t_aff, (256, 256), interpolation = cv2.INTER_NEAREST)
        
        out_t_aff = torch.zeros((self.aff_r, self.aff_r**2,
                                 self.img_size, self.img_size))
        
        out_data = img#np.transpose(img, (2, 0, 1))
        out_t = img_t
        
        out_data = self.transform(out_data)
        out_t = self.transform(out_t)

        for mul in range(5):
            img_t_aff_mul = img_t_aff[0:self.img_size:2**mul,
                                      0:self.img_size:2**mul]
            img_size = self.img_size // (2**mul)

            # 上下左右2pixelずつ拡大
            img_t_aff_mul_2_pix = np.zeros((img_size
                                            + (self.aff_r//2)*2,
                                            img_size
                                            + (self.aff_r//2)*2, 3))
            img_t_aff_mul_2_pix[self.aff_r//2:
                                img_size+self.aff_r//2,
                                self.aff_r//2:
                                img_size+self.aff_r//2] \
                = img_t_aff_mul

            img_t_aff_compare = np.zeros((self.aff_r**2,
                                         img_size, img_size, 3))
            # 1pixelずつずらす
            for i in range(self.aff_r):
                for j in range(self.aff_r):
                    img_t_aff_compare[i*self.aff_r+j] \
                        = img_t_aff_mul_2_pix[i:i+img_size,
                                              j:j+img_size]

            # 同じ色ならAffinity=1(同じ物体)/同じ色でなければAffinity=0(別の物体)
            aff_data = np.where((img_t_aff_compare[:, :, :, 0]
                                 == img_t_aff_mul[:, :, 0])
                                & (img_t_aff_compare[:, :, :, 1]
                                   == img_t_aff_mul[:, :, 1])
                                & (img_t_aff_compare[:, :, :, 2]
                                   == img_t_aff_mul[:, :, 2]), 1, 0)
            aff_data = self.transform(aff_data.transpose(1, 2, 0))
            out_t_aff[mul, :, 0:img_size, 0:img_size] = aff_data
            
            #print('out shape: ', out_data.shape)
            #print('target shape: ', out_t.shape)
            #print('aff shape: ', out_t_aff.shape)
        return out_data, out_t, out_t_aff
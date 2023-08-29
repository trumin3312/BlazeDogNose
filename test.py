import os
import argparse
import torch
import torch.backends.cudnn as cudnn
import numpy as np
import time
from config import cfg_blaze as cfg
from models.module.prior_box import PriorBox
from models.module.py_cpu_nms import py_cpu_nms
import cv2
from models.net_blaze import Blaze
from utils.box_utils import decode, decode_landm
# from utils.timer import Timer

pretrained_dirname = "20230823"
save_dirname = "20230829"

parser = argparse.ArgumentParser(description='Test')
parser.add_argument('-m', '--trained_model', default=f'weights/{pretrained_dirname}/Blaze_Final.pth', type=str, help='Trained state_dict file path to open')
parser.add_argument('--network', default='Blaze', help='Backbone network mobile0.25 or slim or RFB')
parser.add_argument('--origin_size', default=False, type=str, help='Whether use origin image size to evaluate')
parser.add_argument('--long_side', default=1280, help='when origin_size is false, long_side is scaled size(320 or 640 for long side)')
parser.add_argument('--save_folder', default=f'results/{save_dirname}', type=str, help='Dir to save txt results')
parser.add_argument('--cpu', action="store_true", default=False, help='Use cpu inference')
parser.add_argument('--confidence_threshold', default=0.3, type=float, help='confidence_threshold')
parser.add_argument('--top_k', default=2000, type=int, help='top_k')
parser.add_argument('--nms_threshold', default=0.1, type=float, help='nms_threshold')
parser.add_argument('--keep_top_k', default=1000, type=int, help='keep_top_k')
parser.add_argument('--save_image', action="store_true", default=True, help='show detection results')
parser.add_argument('--vis_thres', default=0.3, type=float, help='visualization_threshold')
args = parser.parse_args()


def check_keys(model, pretrained_state_dict):
    ckpt_keys = set(pretrained_state_dict.keys())
    model_keys = set(model.state_dict().keys())
    used_pretrained_keys = model_keys & ckpt_keys
    unused_pretrained_keys = ckpt_keys - model_keys
    missing_keys = model_keys - ckpt_keys
    print('Missing keys:{}'.format(len(missing_keys)))
    print('Unused checkpoint keys:{}'.format(len(unused_pretrained_keys)))
    print('Used keys:{}'.format(len(used_pretrained_keys)))
    assert len(used_pretrained_keys) > 0, 'load NONE from pretrained checkpoint'
    return True


def remove_prefix(state_dict, prefix):
    ''' Old style model is stored with all names of parameters sharing common prefix 'module.' '''
    print('remove prefix \'{}\''.format(prefix))
    f = lambda x: x.split(prefix, 1)[-1] if x.startswith(prefix) else x
    return {f(key): value for key, value in state_dict.items()}


def load_model(model, pretrained_path, load_to_cpu):
    print('Loading pretrained model from {}'.format(pretrained_path))
    if load_to_cpu:
        pretrained_dict = torch.load(pretrained_path, map_location=lambda storage, loc: storage)
    else:
        device = torch.cuda.current_device()
        pretrained_dict = torch.load(pretrained_path, map_location=lambda storage, loc: storage.cuda(device))
    if "state_dict" in pretrained_dict.keys():
        pretrained_dict = remove_prefix(pretrained_dict['state_dict'], 'module.')
    else:
        pretrained_dict = remove_prefix(pretrained_dict, 'module.')
    check_keys(model, pretrained_dict)
    model.load_state_dict(pretrained_dict, strict=False)
    return model


if __name__ == '__main__':
    torch.set_grad_enabled(False)

    net = Blaze(cfg = cfg, phase = 'test')

    net = load_model(net, args.trained_model, args.cpu)
    net.eval()
    print('Finished loading model!')
    #print(net)
    cudnn.benchmark = True
    device = torch.device("cpu" if args.cpu else "cuda")
    net = net.to(device)

    # testing begin
    img_dir = "images/bordercollie"
    for fn in os.listdir(img_dir):
        image_path = os.path.join(img_dir, fn)
    #for i in range(1):
        #image_path = "images/test1.jpg"      
        img_raw = cv2.imread(image_path, cv2.IMREAD_COLOR)
        
        #img_dim = (320, 320)
        img_dim = (128, 128)
        img = cv2.resize(img_raw, img_dim, interpolation=cv2.INTER_LINEAR)
        img = np.float32(img)

        # testing scale
        # target_size = args.long_side
        # max_size = args.long_side

        # im_shape = img.shape
        # im_size_min = np.min(im_shape[0:2])
        # im_size_max = np.max(im_shape[0:2])
        
        # yolo resize
        # img, ratio, (dw, dh) = letterbox(img, (target_size, target_size), color=(104, 117, 123), auto=True, scaleFill=False)
        # resize = np.max(ratio)

        #im_height, im_width, _ = img.shape
        scale = torch.Tensor([img_raw.shape[1], img_raw.shape[0], img_raw.shape[1], img_raw.shape[0]])
        #scale = torch.Tensor([img.shape[1], img.shape[0], img.shape[1], img.shape[0]])
        img -= (104, 117, 123)
        img = img.transpose(2, 0, 1)
        img = torch.from_numpy(img).unsqueeze(0)
        img = img.to(device)
        scale = scale.to(device)

        tic = time.time()
        loc, conf, landms = net(img)  # forward pass
        print('net forward time: {:.4f}'.format(time.time() - tic))

        priorbox = PriorBox(cfg, image_size=img_dim)
        priors = priorbox.forward()
        priors = priors.to(device)
        prior_data = priors.data
        #prior_data = torch.tensor(np.load("anchors.npy"), dtype=torch.float32, device=device)
        #print("prior_data: ", prior_data.shape)
        boxes = decode(loc.data.squeeze(0), prior_data, cfg['variance'])
        #boxes = boxes * scale / resize
        boxes = boxes * scale
        boxes = boxes.cpu().numpy()
        scores = conf.squeeze(0).data.cpu().numpy()[:, 1]
        landms = decode_landm(landms.data.squeeze(0), prior_data, cfg['variance'])
        # scale1 = torch.Tensor([img.shape[3], img.shape[2], img.shape[3], img.shape[2],
        #                        img.shape[3], img.shape[2], img.shape[3], img.shape[2],
        #                        img.shape[3], img.shape[2]])
        # scale1 = scale1.to(device)
        # landms = landms * scale1 / resize
        landms = landms * scale
        landms = landms.cpu().numpy()

        # ignore low scores
        inds = np.where(scores > args.confidence_threshold)[0]
        boxes = boxes[inds]
        landms = landms[inds]
        scores = scores[inds]

        # keep top-K before NMS
        order = scores.argsort()[::-1][:args.top_k]
        boxes = boxes[order]
        landms = landms[order]
        scores = scores[order]

        # do NMS
        dets = np.hstack((boxes, scores[:, np.newaxis])).astype(np.float32, copy=False)
        keep = py_cpu_nms(dets, args.nms_threshold)
        # keep = nms(dets, args.nms_threshold,force_cpu=args.cpu)
        dets = dets[keep, :]
        landms = landms[keep]

        # keep top-K faster NMS
        dets = dets[:args.keep_top_k, :]
        landms = landms[:args.keep_top_k, :]

        dets = np.concatenate((dets, landms), axis=1)

        face_det_list = []
        
        # show image
        if args.save_image:
            for b in dets:
                if b[4] < args.vis_thres:
                    continue
                face_det_list.append(b)
                text = "{:.4f}".format(b[4])
                # b = list(map(lambda x:int(round(x, 0)), b))
                b = list(map(int, b))
                cv2.rectangle(img_raw, (b[0], b[1]), (b[2], b[3]), (0, 0, 255), 2)
                # cx = b[0]
                # cy = b[1] + 12
                # cv2.putText(img_raw, text, (cx, cy), cv2.FONT_HERSHEY_DUPLEX, 0.5, (255, 255, 255))

                # landms
                cv2.circle(img_raw, (b[5], b[6]), 1, (0, 255, 0), 4)
                cv2.circle(img_raw, (b[7], b[8]), 1, (0, 255, 0), 4)
                # cv2.circle(img_raw, (b[5], b[6]), 1, (0, 0, 255), 4)
                # cv2.circle(img_raw, (b[7], b[8]), 1, (0, 255, 255), 4)
                # cv2.circle(img_raw, (b[9], b[10]), 1, (255, 0, 255), 4)
                # cv2.circle(img_raw, (b[11], b[12]), 1, (0, 255, 0), 4)
                # cv2.circle(img_raw, (b[13], b[14]), 1, (255, 0, 0), 4)

                # save image
                #print("face num:", len(face_det_list))
                os.makedirs(args.save_folder, exist_ok=True)
                infer_image_path = os.path.join(args.save_folder, fn)
                # infer_image_path = "results/test1.jpg"
                cv2.imwrite(infer_image_path, img_raw)
import os
from model.openllama import OpenLLAMAPEFTModel
import torch
from torchvision import transforms
from sklearn.metrics import roc_auc_score, precision_recall_curve, auc
from PIL import Image
import numpy as np
import argparse
from skimage import measure

parser = argparse.ArgumentParser("AnomalyGPT", add_help=True)
# paths
parser.add_argument("--few_shot", type=bool, default=False)
parser.add_argument("--k_shot", type=int, default=1)
parser.add_argument("--round", type=int, default=3)

command_args = parser.parse_args()

describles = {}
describles['Hole'] = "This is a photo of a sem for anomaly detection, which should be round, without any damage, flaw, defect, scratch, hole or broken part."
describles['Particle'] = "This is a photo of a sem for anomaly detection, which should be round, without any damage, flaw, defect, scratch, hole or broken part."
describles['Pattern_Deform'] = "This is a photo of a sem for anomaly detection, which should be round, without any damage, flaw, defect, scratch, hole or broken part."
describles['Scratch'] = "This is a photo of a sem for anomaly detection, which should be round, without any damage, flaw, defect, scratch, hole or broken part."

FEW_SHOT = command_args.few_shot

# init the model
args = {
    'model': 'openllama_peft',
    'imagebind_ckpt_path': '/data/yqjiang/project/AnomalyGPT/pretrained_ckpt/imagebind_ckpt/imagebind_huge.pth',
    'vicuna_ckpt_path': '/data/yqjiang/project/AnomalyGPT/pretrained_ckpt/vicuna_ckpt/7b_v0',
    'anomalygpt_ckpt_path': '/data/yqjiang/project/GPTAnomaly-main/code/ckpt/pytorch_model.pt',
    'delta_ckpt_path': '/data/yqjiang/project/AnomalyGPT/pretrained_ckpt/pandagpt_ckpt/7b/pytorch_model.pt',
    'stage': 2,
    'max_tgt_len': 128,
    'lora_r': 32,
    'lora_alpha': 32,
    'lora_dropout': 0.1,
}

model = OpenLLAMAPEFTModel(**args)
delta_ckpt = torch.load(args['delta_ckpt_path'], map_location=torch.device('cpu'))
model.load_state_dict(delta_ckpt, strict=False)
delta_ckpt = torch.load(args['anomalygpt_ckpt_path'], map_location=torch.device('cpu'))
model.load_state_dict(delta_ckpt, strict=False)
model = model.eval().half().cuda()

print(f'[!] init the 7b model over ...')

"""Override Chatbot.postprocess"""
pro_score_list = []
p_auc_list = []
i_auc_list = []
ap_auc_list = []

def threshold_scores(scores, threshold=0.3):
    """将异常得分阈值化为二元图像。"""
    # 对每个帧应用阈值
    return (scores > threshold).astype(int)

def calculate_overlap(true_labels, pred_labels):
    """计算连接组件的重叠度。"""
    overlap_scores = []
    # 在地面真实数据的每个帧中找到所有连接组件
    for region in measure.regionprops(true_labels):
        minr, minc, maxr, maxc = region.bbox
        # 提取对应的预测区域
        pred_region = pred_labels[minr:maxr, minc:maxc]
        # 计算重叠
        intersection = np.sum((pred_region == 1) & (region.image == 1))
        union = np.sum(region.image == 1)
        if union > 0:
            overlap_scores.append(intersection / union)
    return np.mean(overlap_scores) if overlap_scores else 0

def pro_metric_pixel_level(ground_truth, anomaly_scores, threshold=0.3):
    """计算像素级 PRO 指标。"""
    frames_score = []
    # 独立处理每个帧
    for i in range(ground_truth.shape[0]):
        # 对当前帧的异常得分进行阈值处理
        pred_labels = threshold_scores(anomaly_scores[i], threshold)
        # 将地面实况中的异常区域标记为连接组件
        true_labels = measure.label(ground_truth[i], connectivity=2)
        # 计算当前帧中所有连接组件的平均重叠度
        frame_score = calculate_overlap(true_labels, pred_labels)
        frames_score.append(frame_score)
    # 计算所有帧的平均得分
    return np.mean(frames_score)

def threshold_predictions(scores):
    """根据指定阈值将预测得分转换为二元标签"""
    return (scores > 0.3).astype(int)

def calculate_ap(ground_truth, prediction_scores):
    """计算像素级平均精度(AP)。"""

    # 将预测得分根据阈值转换为二元标签
    binary_predictions = threshold_predictions(prediction_scores)
    
    # 将输入展平
    ground_truth = ground_truth.flatten()
    binary_predictions = binary_predictions.flatten()
    
    # 计算精确率和召回率
    precision, recall, _ = precision_recall_curve(ground_truth, binary_predictions)
    
    # 计算AP值
    ap = auc(recall, precision)
    return ap

def predict(
        input,
        image_path,
        normal_img_path,
        max_length,
        top_p,
        temperature,
        history,
        modality_cache,
):
    prompt_text = ''
    for idx, (q, a) in enumerate(history):
        if idx == 0:
            prompt_text += f'{q}\n### Assistant: {a}\n###'
        else:
            prompt_text += f' Human: {q}\n### Assistant: {a}\n###'
    if len(history) == 0:
        prompt_text += f'{input}'
    else:
        prompt_text += f' Human: {input}'

    response, pixel_output = model.generate({
        'prompt': prompt_text,
        'image_paths': [image_path] if image_path else [],
        'audio_paths': [],
        'video_paths': [],
        'thermal_paths': [],
        'normal_img_paths': normal_img_path if normal_img_path else [],
        'top_p': top_p,
        'temperature': temperature,
        'max_tgt_len': max_length,
        'modality_embeds': modality_cache
    })

    return response, pixel_output


input = "Is there any anomaly in the image?"
root_dir = '/data/yqjiang/project/GPTAnomaly-main/data/anomalygpt/SEM-WaD'

mask_transform = transforms.Compose([
    transforms.Resize((224, 224)),
    transforms.ToTensor()
])

CLASS_NAMES = ['Hole', 'Particle', 'Pattern_Deform', 'Scratch']

precision = []

for c_name in CLASS_NAMES:
    normal_img_paths = [
        "/data/yqjiang/project/GPTAnomaly-main/data/anomalygpt/SEM-WaD/" + c_name + "/train/good/" + str(command_args.round * 4).zfill(3) + ".png"]
    normal_img_paths = normal_img_paths[:command_args.k_shot]
    right = 0
    wrong = 0
    p_pred = []
    p_label = []
    i_pred = []
    i_label = []
    for root, dirs, files in os.walk(root_dir):
        for file in files:
            file_path = os.path.join(root, file)
            if "test" in file_path and 'png' in file and c_name in file_path:
                if FEW_SHOT:
                    resp, anomaly_map = predict(describles[c_name] + ' ' + input, file_path, normal_img_paths, 512, 0.1,
                                                1.0, [], [])
                else:
                    resp, anomaly_map = predict(describles[c_name] + ' ' + input, file_path, [], 512, 0.1, 1.0, [], [])
                is_normal = 'good' in file_path.split('/')[-2]

                if is_normal:
                    img_mask = Image.fromarray(np.zeros((224, 224)), mode='L')
                else:
                    mask_path = file_path.replace('test', 'ground_truth')
                    mask_path = mask_path.replace('.png', '_mask.png')
                    img_mask = Image.open(mask_path).convert('L')

                img_mask = mask_transform(img_mask)
                img_mask[img_mask > 0.1], img_mask[img_mask <= 0.1] = 1, 0
                img_mask = img_mask.squeeze().reshape(224, 224).cpu().numpy()

                anomaly_map = anomaly_map.reshape(224, 224).detach().cpu().numpy()

                p_label.append(img_mask)
                p_pred.append(anomaly_map)

                i_label.append(1 if not is_normal else 0)
                i_pred.append(anomaly_map.max())

                position = []

                if 'good' not in file_path and 'Yes' in resp:
                    right += 1
                elif 'good' in file_path and 'No' in resp:
                    right += 1
                else:
                    wrong += 1

    p_pred = np.array(p_pred)
    p_label = np.array(p_label)

    i_pred = np.array(i_pred)
    i_label = np.array(i_label)

    pro_score = round(pro_metric_pixel_level(p_label, p_pred) * 100, 2)
    ap_score = round(calculate_ap(p_label, p_pred) * 100, 2)

    p_auroc = round(roc_auc_score(p_label.ravel(), p_pred.ravel()) * 100, 2)
    i_auroc = round(roc_auc_score(i_label.ravel(), i_pred.ravel()) * 100, 2)

    pro_score_list.append(pro_score)
    ap_auc_list.append(ap_score)
    p_auc_list.append(p_auroc)
    i_auc_list.append(i_auroc)
    precision.append(100 * right / (right + wrong))

    print(c_name, 'right:', right, 'wrong:', wrong)
    print(c_name, "PRO:", pro_score)
    print(c_name, "AP:", ap_score)
    print(c_name, "i_AUROC:", i_auroc)
    print(c_name, "p_AUROC:", p_auroc)

print("PRO:", torch.tensor(pro_score_list).mean())
print("AP:", torch.tensor(ap_auc_list).mean())
print("i_AUROC:", torch.tensor(i_auc_list).mean())
print("p_AUROC:", torch.tensor(p_auc_list).mean())
print("precision:", torch.tensor(precision).mean())
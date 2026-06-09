import torch
import numpy as np
from PIL import Image
import cv2
import matplotlib.pyplot as plt
import numpy as np
from PIL import Image
# from utils.transformer_new import UnNormalizer
from PIL import Image
import torchvision.transforms as transforms
import cv2
import random
from matplotlib.cm import get_cmap

# def show_feature_map(image, conv_features,diff = '3'):
#     '''可视化卷积层特征图输出
#     img_src:源图像文件路径
#     conv_feature:得到的卷积输出,[b, c, h, w]
#     '''
#     # img = Image.open(img_src).convert('RGB')
#     print(conv_features.size())
#     # unnormalize = UnNormalizer()
#     img = image.cpu()
#     # img = np.array(255 * unnormalize(image[0, :, :, :])).copy()

#     img[img < 0] = 0
#     img[img > 255] = 255

#     img = np.transpose(img, (1, 2, 0))

#     image = Image.fromarray(img.astype(np.uint8)).convert('RGB')

#     height, width = image.size

#     conv_features = conv_features.cpu()

#     heat = conv_features.squeeze(0)  # 降维操作,尺寸变为(C,H,W)
#     heatmap = torch.mean(heat, dim=0)  # 对各卷积层(C)求平均值,尺寸变为(H,W)
#     # heatmap = torch.max(heat,dim=1).values.squeeze()

#     heatmap = heatmap.numpy()  # 转换为numpy数组
#     #heatmap = np.maximum(heatmap, 0)
#     heatmap /= np.max(heatmap)  # minmax归一化处理

#     heatmap1 = np.uint8(255 * heatmap)  # 像素值缩放至(0,255)之间,uint8类型,这也是前面需要做归一化的原因,否则像素值会溢出255(也就是8位颜色通道)
#     heatmap1 = cv2.applyColorMap(heatmap1, cv2.COLORMAP_HSV)  # 颜色变换
#     plt.imshow(heatmap1)
#     plt.savefig('feature/feature1_' + diff + '.png')
#     plt.show()

#     heatmap = cv2.resize(heatmap, (height, width))  # 变换heatmap图像尺寸,使之与原图匹配,方便后续可视化
#     heatmap = np.uint8(255 * heatmap)  # 像素值缩放至(0,255)之间,uint8类型,这也是前面需要做归一化的原因,否则像素值会溢出255(也就是8位颜色通道)
#     heatmap = cv2.applyColorMap(heatmap, cv2.COLORMAP_HSV)  # 颜色变换
#     plt.imshow(heatmap)
#     plt.savefig('feature/feature_' + diff + '.png')
#     plt.show()

#     # heatmap = np.array(Image.fromarray(heatmap).convert('L'))
#     superimg = heatmap * 0.4 + np.array(image)[:, :, ::-1]  # 图像叠加，注意翻转通道，cv用的是bgr
#     cv2.imwrite('feature/feature_image_'+diff+'.png', superimg)  # 保存结果
#     # 可视化叠加至源图像的结果
#     img_ = np.array(Image.open('feature/feature_image_'+diff+'.png').convert('RGB'))
#     plt.imshow(img_)
#     plt.savefig('feature/feature_image_'+diff+'.png')
#     plt.show()

def show_feature_map(img_src, conv_features, diff='3'):
    '''可视化卷积层特征图输出
    img_src: 源图像文件路径
    conv_features: 得到的卷积输出，形状为[b, c, h, w]
    '''
    conv_features_cpu = conv_features.detach().cpu()
    # 加载源图像并转换为RGB格式
    image = Image.open(img_src).convert('RGB')

    # 将PIL图像转换为NumPy数组
    img_np = np.array(image)

    # 提取卷积特征并进行处理
    conv_features = conv_features_cpu.squeeze(0)  # 降维，形状变为[c, h, w]

    heatmap = torch.mean(conv_features, dim=0).numpy()  # 对各卷积层求平均值，形状变为[h, w]
    # heatmap = np.maximum(heatmap, 0)
    # heatmap /= np.max(heatmap)
    heatmap = (heatmap - heatmap.min()) / (heatmap.max() - heatmap.min())

    # # 创建热图
    # heatmap = np.uint8(255 * heatmap)
    # heatmap = cv2.applyColorMap(heatmap, cv2.COLORMAP_HSV)
    # heatmap = cv2.resize(heatmap, (image.width, image.height))  # 调整热图大小以匹配原图

    # # 叠加热图到原图
    # superimg = heatmap * 0.4 + img_np[:, :, ::-1]  # 注意：OpenCV使用BGR格式
    # 创建热力图
    cmap = get_cmap('jet')  # 使用'jet'颜色映射
    heatmap = cmap(heatmap)[:, :, :3]  # 取RGB通道
    heatmap = (heatmap * 255).astype(np.uint8)  # 转换为0-255的整数
    heatmap_image = Image.fromarray(heatmap).resize(image.size, Image.BILINEAR)  # 调整大小
    
    # 叠加原图和热力图，这里使用alpha=0.5表示半透明
    overlay_image = Image.blend(image, heatmap_image, alpha=0.5)
    # overlay_image.save('/data/yqjiang/project/AnomalyGPT/feature/img_{diff}.png')
    save_path = '/data/yqjiang/project/AnomalyGPT/noise/img.png'
    overlay_image.save(save_path)

    # 保存并显示结果
    # cv2.imwrite(f'/data/yqjiang/project/AnomalyGPT/feature/img_{diff}.png', overlay_image)
    # plt.imshow(cv2.cvtColor(superimg, cv2.COLOR_BGR2RGB))  # 将BGR转换回RGB进行显示
    # plt.show()


if __name__ == '__main__':
    random_tensor = torch.randn(1, 2, 224, 224)
    # 图片路径
    image_path = '/data/yqjiang/project/AnomalyGPT/data/all_anomalygpt/MVTec-AD/bottle/test/broken_small/009.png'
    
    show_feature_map(image_path, random_tensor)


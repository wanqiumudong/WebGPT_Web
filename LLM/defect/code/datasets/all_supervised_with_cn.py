import os
from typing import Optional, Callable
from torch.utils.data import Dataset, DataLoader

import cv2
import numpy as np
import torch
import torchvision.transforms as transforms
from PIL import Image
from matplotlib import pyplot as plt
import random


def find_contours(image):
    _, binary_image = cv2.threshold(image, 127, 255, cv2.THRESH_BINARY)

    contours, _ = cv2.findContours(binary_image, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    centers = []

    for contour in contours:
        M = cv2.moments(contour)
        if M["m00"] != 0:
            center_x = int(M["m10"] / M["m00"])
            center_y = int(M["m01"] / M["m00"])
            centers.append((center_x, center_y))

    return centers

describles = {}
describles['SEM-image'] = "This is an SEM image at the COREPW-STRIP step showing a particle defect. The cause of this problem is particles fell during the previous manufacturing process. The proposed solution is to perform a scribe on all wafers during the WET stage to remove these particles."

describles_cn = {}
describles_cn['SEM-image'] = "这是在COREPW-STRIP步骤中的一张SEM图像，显示了一个颗粒缺陷。造成这一问题的原因是在之前的制造过程中掉落的颗粒。建议的解决方案是在WET阶段对所有晶片进行划线，以清除这些颗粒。"

CLASS_NAMES = ['SEM-image', 'bottle', 'cable', 'capsule', 'carpet', 'grid',
               'hazelnut', 'leather', 'metal_nut', 'pill', 'screw',
               'tile', 'toothbrush', 'transistor', 'wood', 'zipper',
               'candle', 'capsules', 'cashew', 'chewinggum', 'fryum',
               'macaroni1', 'macaroni2', 'pcb1', 'pcb2', 'pcb3', 'pcb4',
               'pipe_fryum', 'Hole', 'Particle', 'Pattern_Deform', 'Scratch']

MULTI_CLASS = [
    'candle', 'capsules', 'macaroni1', 'macaroni2'
]

Chinese_position = {
    'top': '上方',
    'top left': '左上方',
    'top right': '右上方',
    'bottom': '下方',
    'bottom left': '左下方',
    'bottom right': '右下方',
    'center': '中间',
    'left': '左侧',
    'right': '右侧'
}

Chinese_class_names = {'SEM-image': ["扫描电子显微镜下的晶圆图", "晶圆图"], 'bottle': ["瓶子", "罐子"], 'cable': ["电线", '电缆'],
                       'capsule': ["药丸", "胶囊"], 'carpet': ['地毯', '织物'], 'grid': ['铁丝网', '防护栏'],
                       'hazelnut': ['榛子', '栗子', "果实"], 'leather': ['皮革'], 'metal_nut': ['金属原件'],
                       'pill': ['药片'], 'screw': ['钉子'],
                       'tile': ['瓷砖', '地砖'], 'toothbrush': ['牙刷'], 'transistor': ['晶体管', '电子元件'],
                       'wood': ['木头'], 'zipper': ['拉链'],
                       'candle': ['蜡烛'], 'capsules': ["药丸", "胶囊"], 'cashew': ['腰果'], 'chewinggum': ['口香糖'],
                       'fryum': ['元件', '元器件', '样品', '样本'],
                       'macaroni1': ['元件', '元器件', '样品', '样本'], 'macaroni2': ['元件', '元器件', '样品', '样本'],
                       'pipe_fryum': ['元件', '元器件', '样品', '样本'], 'Hole': ["扫描电子显微镜图", "晶圆图"],
                       'Particle': ["扫描电子显微镜图", "晶圆图"], 'Pattern_Deform': ["扫描电子显微镜图", "晶圆图"], 
                       'Scratch': ["扫描电子显微镜图", "晶圆图"],}

class_questions = [
    'This is an image for anomaly detection. What is the content of the image?',
    "What's the object in the image?",
    "What's this in the image?",
    "Describe this image.",
    "Take a look at this image and describe what you notice.",
    "Please provide a description of the picture.",
    "Could you describe the contents of this image for me?",
    "Can you identify the elements present in the image?"
    "What can you observe in this picture?",
    "Describe the objects shown in the image.",
    "Could you list the items visible in this image?",
    "What do you see in the picture?",
    "Identify the various components of this image.",
    "What is depicted in the photograph?",
    "Provide a rundown of the contents of this image.",
    "What's the subject matter of this image?",
    "Enumerate the objects that can be spotted in this image.",
    "Describe the visual elements within the picture.",
    "What visual information can you extract from this image?",
    "What elements compose the scene in the image?",
    "Please give a verbal depiction of the image.",
    "From your perspective, what is shown in the image?",
    "Could you break down the objects present in the picture?",
    "Summarize the contents of the image in your own words.",
    "What details can you identify within the image?",
    "Provide a textual account of the image's contents.",
    "Based on the image, can you discern any notable features?"
]

class_questions_cn = [
    "这是一张用于异常检测的图像。图像内容是什么？",
    "图像中有什么物体？",
    "图像中是什么东西？",
    "描述一下这张图片。",
    "请看一下这张图片，描述你注意到的内容。",
    "请提供这张图片的描述。",
    "你能描述一下这张图片的内容吗？",
    "你能识别出图像中的元素吗？",
    "你能在这张图片中看到什么？",
    "描述图像中展示的物体。",
    "你能列举出这张图片中可见的物品吗？",
    "你在图片里看到了什么？",
    "识别出图像中的各个组成部分。",
    "照片中描绘了什么？",
    "简要介绍一下这张图片的内容。",
    "这张图片的主题是什么？",
    "列举出这张图片中可以看到的物体。",
    "描述图片中的视觉元素。",
    "你能从这张图片中提取出什么视觉信息？",
    "图像中有哪些元素构成了场景？",
    "请用口头方式描述这张图片。",
    "从你的角度来看，这张图片展示了什么？",
    "你能分解出图片中存在的物体吗？",
    "用你自己的话概括一下图片的内容。",
    "你能在图像中识别出哪些细节？",
    "用文字叙述一下图片的内容。",
    "基于这张图片，你能辨别出哪些显著的特征吗？"
]

single_answers = [
    'This in the image is {}.',
    'What you\'re seeing here is {}.',
    'In this image, the featured object is {}.',
    '{} is visible in this picture.',
    'The object captured in the image is {}.',
    'The highlighted item is {}.',
    'It appears to be {} in the image.',
    'You\'re looking at {} in this photograph.',
    'This is none other than {}.',
    'The image showcases {}.',
    'What\'s presented here is {}.',
    'The focus is on {} in this image.',
    '{} is what we have in the image.',
    'The photographed subject is {}.',
    'This image contains {}.',
    'The visible entity is {}.',
    'The image encapsulates {}.',
    'The main subject here is {}.',
    'The image portrays {}.',
    'The item captured is {}.'
]

single_answers_cn = [
    "在图像中，这个物体是{}。",
    "你看到的是{}。",
    "在这张图片里，焦点放在了{}上。",
    "这张照片中展现出一个{}。",
    "图中的物体就是一个{}。",
    "图中突出显示的是一个{}。",
    "图中似乎是一个{}。",
    "这是{}。",
    "这张图片展示了一个{}。",
    "这里展现的是一个{}。",
    "图中重点呈现的是一个{}。",
    "图中的{}是我们所关注的。",
    "照片中的主要内容是{}。",
    "这张图片呈现了一个{}。",
    "图中可见的实体是{}。",
    "这张图片包含了{}。",
    "这张图片主要展现了{}。",
    "图片中描绘了{}。",
    "图片中拍摄到的物品是{}。"
]

multi_answers = [
    'In the image, there are several {}.',
    'You can spot multiple instances of {}.',
    'What you\'re seeing here is a collection of {}.',
    'A variety of {} are visible in this picture.',
    'The image captures several {}.',
    'The highlighted objects are {}.',
    'You\'ll notice a group of {} in this image.',
    'This photograph features several {}.',
    'The scene is filled with {}.',
    'Multiple instances of {} are depicted here.',
    'The image showcases an assortment of {}.',
    'What\'s presented here is a multitude of {}.',
    'In this image, numerous {} can be observed.',
    'The photographed scene contains several {}.',
    'This image encapsulates a number of {}.',
    'The visible entities are {}.',
    'The image portrays a variety of {}.',
    'You\'re looking at multiple {} in this photograph.',
    'Several instances of {} are what we have in the image.',
    'The items captured are {}.'
]

multi_answers_cn = [
    "在图像中，有几个{}。",
    "你可以看到多个{}的实例。",
    "你在这里看到的是一组{}的集合。",
    "这张图片中可见多种类型的{}。",
    "图像捕捉到了几个{}。",
    "突出显示的物体是{}。",
    "你会注意到这张图片中有一组{}。",
    "这张照片中展示了几个{}。",
    "场景中充满了{}。",
    "这里描绘了多个{}的情景。",
    "这张图片展示了各种各样的{}。",
    "这里呈现的是多种{}的众多实例。",
    "在这张图片中，你可以观察到许多{}。",
    "所拍摄的场景包含了几个{}。",
    "这张图片涵盖了若干个{}。",
    "图中可见的实体是{}。",
    "这张图片描绘了多种{}。",
    "你在这张照片中看到了多个{}。",
    "图中展现了几个{}的实例。",
    "图中所拍摄到的物品是{}。"
]

class_questions_cause = [
    'How was this defect caused?',
    'What factors led to the occurrence of this defect?',
    'What caused this defect to arise?',
    'What is responsible for the existence of this problem?',
    'What factors contributed to the occurrence of this defect?',
    'What are the reasons behind the occurrence of this defect?',
    'What led to the generation of this defect?',
    'What is the fundamental reason behind this issue?',
    'Which factors triggered the emergence of this defect?',
    'Why did this defect occur?'
]

class_questions_cause_solve = [
    'How can we address these deficiencies?',
    'How to avoid these defects?',
    'How can we avoid the occurrence of these defects in the production process?',
    'What are the methods for resolving these shortcomings?',
    'What measures should we take to overcome these deficiencies?',
    'Are there viable solutions for these shortcomings?',
    'What steps should be taken to rectify these deficiencies?',
    'How can we effectively fix these flaws?',
    'Are there practical solutions for addressing these shortcomings?',
    'What measures can we adopt to compensate for the existence of these deficiencies?',
    'What strategies can be employed to tackle these deficiencies?',
    'What methods are available for resolving these shortcomings?',
    'How to address these deficiencies?'
]

class_questions_cause_cn = [
    "这个缺陷是如何造成的？",
    "是什么因素导致了这个缺陷的发生？",
    "是什么引起了这个缺陷的产生？",
    "是什么导致了这个问题的存在？",
    "是哪些因素促使了这个缺陷的发生？",
    "造成这一缺陷的原因是什么？",
    "是什么导致了这个缺陷的产生？",
    "这个问题的根本原因是什么？",
    "是哪些因素引起了这个缺陷？",
    "为什么会出现这个缺陷？"
]

class_questions_cause_solve_cn = [
    "我们应该如何解决这些缺陷？",
    "如何避免这些缺陷?",
    "我们在生产过程中如何避免这些缺陷的发生?",
    "如何解决这些不足之处的方法是什么？",
    "我们应该采取什么措施来克服这些缺陷？",
    "这些不足是否有可行的解决方案？",
    "针对这些缺陷，应该采取哪些步骤来弥补？",
    "如何有效地修复这些缺陷？",
    "针对这些不足，有没有实用的解决方案？",
    "我们可以采取哪些措施来弥补这些缺陷的存在？",
    "针对这些缺陷，可以采用哪些策略？",
    "有哪些方法可以解决这些不足？",
    "如何解决这些不足？"
]

single_answers_cause = {
    'scratch': ['This is an SEM image after the CMP process showing a scratch defect, where it is suspected that a lower number of runs has led to slurry sedimentation, potentially causing scratches on the wafer surface.',
                'The clear presence of scratch defects in this SEM image suggests slurry deposition may have occurred after the CMP process, possibly due to minimal running cycles.',
                'This is an SEM image taken after the CMP process, showing a scratch defect, suspected to be caused by particle residues during cleaning, potentially scratching the wafer surface.',
                'This is an SEM image taken after the CMP process, showing a scratch defect, suspected to result from excessive mechanical friction, leaving scratches on the wafer surface.'],
    'hole': ['This is an SEM image showing a hole defect. This defect is likely a result of particles dislodged during previous process steps, which have been removed in the current stage, manifesting as holes.',
             'In this scanning electron microscope image, pore defects are visible, likely formed by the clearing of particles that had fallen off in previous steps at this stage.'],
    'particle': ['This is an SEM image at the COREPW-STRIP step showing a particle defect. The cause of this problem is particles fell during the previous manufacturing process.',
                 'This SEM image taken at the COREPW-STRIP stage shows a particle defect, which is highly likely due to particles that had fallen off in an earlier manufacturing step.'],
    'pattern_deform': ['This is a SEM image of pattern_deform. The cause of this defect may be a window tolerance issue with the etching (ET) recipe.',
                       'This SEM image exhibits pattern distortion, a defect that could be due to window tolerance issues in the etching (ET) technique.']
}


single_answers_cause_cn = {
    'scratch': ['这是 CMP 工艺后的一张 SEM 图像，显示了一个划痕缺陷，怀疑是较少的运行次数导致浆料沉积，从而可能在晶片表面造成划痕。',
                '划痕缺陷在此 SEM 图像中清晰可见，表明 CMP 工艺后可能发生了浆料沉积，这可能是由于运行次数较少而导致的。',
                '这是 CMP 工艺后的一张 SEM 图像，显示了一个划痕缺陷，怀疑是清洗过程中颗粒物残留导致的，从而可能在晶片表面造成划痕。',
                '这是 CMP 工艺后的一张 SEM 图像，显示了一个划痕缺陷，怀疑是机械摩擦过大引起的，从而在晶片表面留下了划痕。'],
    'hole': ['这是一张显示孔洞缺陷的 SEM 图像。这种缺陷很可能是由于在之前的工艺步骤中脱落的颗粒在当前阶段被清除而形成的孔洞。',
             '在这张扫描电子显微镜图像中，可以看到孔洞缺陷，这些缺陷很可能是在当前阶段清除了之前步骤中掉落的颗粒而形成的。'],
    'particle': ['这是在 COREPW-STRIP 步骤中的一张 SEM 图像，显示了一个颗粒缺陷。造成这一问题的原因是在之前的制造过程中掉落的颗粒。',
                 '这张位于COREPW-STRIP阶段的SEM图像展示了颗粒缺陷，很可能是由于早先制造步骤中脱落的颗粒所致。'],
    'pattern_deform': ['这是一张图案变形的SEM图片。造成该缺陷的原因可能是蚀刻（ET）方法的窗口容差问题。',
                       '这张SEM图像显示了图案的变形，这种缺陷可能由蚀刻技术（ET）中的窗口公差不准引起。']
}


single_answers_cause_solve = {
    'scratch': ['Measures such as reducing the filter size and implementing a water curtain may be attempted to prevent such defects.',
                'To prevent such defects, reducing the size of the filter and utilizing methods like water curtains can be considered.'],
    'hole': ['To address this issue, specification settings by the PIE and CVD teams are needed. Additionally, preventive maintenance of the CVD machine may be conducted to prevent similar defects in the future.',
             'To address this issue, standardized operations by the PIE and CVD teams are required. Additionally, preventative maintenance of CVD equipment can also prevent future similar defects.'],
    'particle': ['The proposed solution is to perform a scribe on all wafers during the WET stage to remove these particles.',
                 'To eliminate particles, it is recommended to perform scribing on all wafers during the WET step.'],
    'pattern_deform': ['To solve this problem the etching recipe can be modified to accommodate the APF (Advanced Patterning Film) process.',
                       'To overcome this challenge, the etching method can be redesigned to be compatible with the Advanced Patterning Film (APF) process.']
}


single_answers_cause_solve_cn = {
    'scratch': ['可尝试缩小过滤器尺寸和采用水帘等措施来防止出现此类缺陷。',
                '为了防止此类缺陷，可以考虑缩减过滤器的大小并使用水幕等方法。'],
    'hole': ['要解决这个问题，需要 PIE 和 CVD 团队进行规范设置。此外，还可对 CVD 机器进行预防性维护，以防止今后出现类似缺陷。',
             '解决此问题需通过PIE和CVD团队的标准化操作。同时，对CVD设备执行预防性保养也能避免未来的相似缺陷。'],
    'particle': ['建议的解决方案是在 WET 阶段对所有晶片进行划线，以清除这些颗粒。',
                 '为了清除颗粒，建议在WET步骤中对所有晶圆执行划线操作。'],
    'pattern_deform': ['要解决这个问题可以修改蚀刻方法来适应APF（Advanced Patterning Film）工艺。',
                       '要克服这一挑战，可以重新设计蚀刻方法以适用于APF（高级图案化膜）工艺。']
}

questions_dict = {
    1: "What is the integrated circuit manufacturing process (CMP)?",
    2: "What are the main steps of the integrated circuit manufacturing process?",
    3: "What is the photolithography process?",
    4: "What is the etching process?",
    5: "What is the deposition process?",
    6: "What is the ion implantation process?",
    7: "Why is the annealing process necessary?",
    8: "How are process parameters chosen in the integrated circuit manufacturing process?",
    9: "How is process control implemented in integrated circuit manufacturing?",
    10: "How are defects in the manufacturing process handled?",
    11: "What is the role of process innovation in integrated circuit manufacturing?",
    12: "What factors are considered when selecting materials in integrated circuit manufacturing?",
    13: "How is the integrated circuit manufacturing process evaluated and optimized?",
    14: "What are the future trends in the development of integrated circuit manufacturing processes?",
    15: "Recommend a good mobile app.",
    16: "Any motivational quotes to share?",
    17: "How to overcome procrastination?",
    18: "Any good weekend getaways?",
    19: "How to learn a new language?",
    20: "Any fitness advice?",
    21: "What's your view on the future of AI?",
    22: "How to use social media correctly?",
    23: "Any recommended breakfast?",
    24: "Favorite season?",
    25: "How to maintain a positive mindset?",
    26: "How to use time correctly?",
    27: "Any recommended healthy snacks?",
    28: "Do you like watching movies?",
    29: "How to develop a reading habit?",
    30: "Any travel tips?",
    31: "How to deal with work stress?",
    32: "Tea or coffee?",
    33: "How to foster creativity?",
    34: "Recommend a good restaurant?",
    35: "Any shopping advice?",
    36: "How to overcome fear of public speaking?",
    37: "Do you have a favorite movie genre?",
    38: "How to balance social life?",
    39: "Any recommended fitness activities?",
    40: "How to learn to relax?",
    41: "Have you ever participated in a gaming competition?",
    42: "Recommend a motivational book.",
    43: "How to overcome learning difficulties?",
    44: "Any good learning tools to recommend?",
    45: "What are your thoughts on environmental protection?",
    46: "How to maintain good communication?",
    47: "What are your expectations for the future?",
    48: "How to build good teamwork at work?",
    49: "Any travel memories to share?",
    50: "Any interesting scientific discoveries recently?",
    51: "What's your favorite type of cuisine?",
    52: "Any recommendations for productivity tools?",
    53: "How to stay focused while working or studying?",
    54: "Do you have any favorite quotes from movies?",
    55: "How do you handle a busy schedule?",
    56: "Any advice for effective time management?",
    57: "What's your opinion on the importance of hobbies?",
    58: "Recommend a documentary worth watching.",
    59: "How to stay motivated during challenging times?",
    60: "Do you have any favorite apps for meditation?",
    61: "What's your perspective on the impact of social media on society?",
    62: "Any tips for improving memory retention?",
    63: "How to start a blog and gain readership?",
    64: "What's the best way to learn a musical instrument?",
    65: "Do you prefer indoor or outdoor activities?",
    66: "Any suggestions for handling conflicts in relationships?",
    67: "What's the most interesting place you've ever visited?",
    68: "How to develop effective communication skills?",
    69: "Any recommended strategies for public speaking anxiety?",
    70: "Do you follow a specific morning routine?",
    71: "What's your approach to handling stress at work?",
    72: "Any favorite podcasts you would recommend?",
    73: "How to encourage a reading habit in children?",
    74: "What's the best way to learn a new programming language?",
    75: "Any thoughts on the role of technology in education?",
    76: "How to set and achieve personal goals effectively?",
    77: "What's your favorite type of exercise?",
    78: "Any advice for someone starting a new job?",
    79: "What's your preferred method for relaxation?",
    80: "Do you have any favorite board games or card games?",
    81: "How to choose a career that aligns with your passion?",
    82: "Any tips for maintaining a positive team culture at work?",
    83: "What's the best way to save money for future goals?",
    84: "How to overcome writer's block when writing?",
    85: "Do you have any favorite science fiction books?",
    86: "What's your perspective on the importance of self-care?",
    87: "Any recommendations for a weekend getaway with friends?",
    88: "How to handle criticism in a constructive way?",
    89: "What's your favorite type of dessert?",
    90: "Do you believe in setting New Year's resolutions?",
    91: "How to break a bad habit and form a good one?",
    92: "Any tips for effective team collaboration in a remote work setup?",
    93: "What's your favorite memory from childhood?",
    94: "How to deal with information overload in the digital age?",
    95: "Do you have any favorite historical documentaries?",
    96: "What's your approach to staying organized in daily life?",
    97: "Any suggestions for a successful job interview?",
    98: "How to encourage creativity in a team environment?",
    99: "What's your favorite way to spend a lazy Sunday?",
    100: "Do you have any favorite travel destinations off the beaten path?"
}

answers_dict = {
    1: "The integrated circuit manufacturing process refers to the process of integrating electronic devices (such as transistors and capacitors) onto a silicon wafer and forming a complete integrated circuit chip through a series of processing steps.",
    2: "The main steps include wafer preparation, cleaning, photolithography, etching, deposition, ion implantation, annealing, and cutting.",
    3: "The photolithography process projects chip patterns onto a silicon wafer by combining photoresist and photomask, determining the patterns in different regions of the chip.",
    4: "The etching process removes unwanted material from the silicon wafer, commonly used to form trenches, holes, and other structures.",
    5: "The deposition process adds new materials to the silicon wafer's surface, often used to form metal lines and insulating layers.",
    6: "The ion implantation process injects ions into the silicon wafer's surface, altering its electrical properties by controlling the energy and dose of the ions.",
    7: "The annealing process involves heating the silicon wafer to change its internal structure and properties, making it more suitable for integrated circuits.",
    8: "Process parameters are chosen based on chip performance requirements, cost, and manufacturing equipment capabilities, often requiring extensive experiments and optimization.",
    9: "Process control is implemented through strict process specifications, real-time monitoring, and feedback control to ensure each process step meets the requirements.",
    10: "Handling defects typically involves defect analysis to identify their causes and taking corrective measures to repair or adjust the process.",
    11: "Process innovation can bring higher production efficiency, lower costs, and better product performance, serving as a key driver for technological advancements in integrated circuit manufacturing.",
    12: "Material selection considers factors such as electrical performance, mechanical properties, and high-temperature resistance, along with cost and reliability.",
    13: "Evaluating and optimizing processes involve experiments, simulations, and data analysis to identify the best combination of process parameters to meet design requirements.",
    14: "Future trends include further miniaturization of process dimensions, the application of new materials, and advancements in process automation and intelligence.",
    15: "Evernote is a user-friendly note-taking app that can help you organize information easily.",
    16: "'Success is not something in the future; it's every day.'",
    17: "Break tasks into smaller steps, make clear plans, and gradually complete tasks to overcome procrastination.",
    18: "Consider walking in a nearby park, attending social events, or trying new restaurants for a pleasant weekend.",
    19: "Practice daily, use language learning apps, and engage in conversations with native speakers to improve language skills.",
    20: "Combine aerobic exercise and strength training, exercise regularly, and maintain overall physical health.",
    21: "The future of AI may involve broader applications, but ethical and privacy issues need careful consideration.",
    22: "Stay rational, manage privacy settings, avoid excessive use, and selectively follow valuable content for a positive experience.",
    23: "Have a balanced breakfast, including protein, vegetables, and fruits, to provide the necessary energy for the day.",
    24: "I can't sense seasons, but many people enjoy the warmth and blooming flowers in spring.",
    25: "Cultivate gratitude, focus on positive things, and associate with optimistic people to maintain a positive mindset.",
    26: "Create a reasonable schedule, set priorities, and allocate time wisely to avoid wasting time.",
    27: "Nuts, fruits, yogurt, etc., are healthy snack choices.",
    28: "I can't experience entertainment, but I can recommend good movies for you.",
    29: "Read regularly at scheduled times, choose books of interest, and gradually develop a passion for reading.",
    30: "Plan your itinerary, try local cuisine, and interact with locals to make your travels more enriching.",
    31: "Learn to allocate tasks reasonably, take regular breaks, and find stress-relief methods like exercise or meditation.",
    32: "I don't have taste preferences, but both tea and coffee have their own charms; choose based on personal taste.",
    33: "Stay curious, try new things, and think about different solutions to problems to foster creativity.",
    34: "It depends on your city, but you can try searching for local food reviews to find popular restaurants.",
    35: "Make a shopping list, watch for discounts and promotions, avoid impulsive shopping to shop rationally.",
    36: "Practice public speaking, relax with deep breaths, focus on sharing valuable information to overcome the fear.",
    37: "I don't have personal preferences, but sci-fi, comedy, and drama are popular movie genres.",
    38: "Regularly socialize with friends, stay connected, but also allocate some personal time.",
    39: "Running, yoga, swimming, etc., are effective fitness activities; choose the one that suits you.",
    40: "Try meditation, listen to music, read, etc., to find a relaxation method that suits you.",
    41: "I'm a program and can't participate in gaming competitions, but I can provide gaming recommendations.",
    42: "'The Alchemist' is a widely loved motivational book that's worth reading.",
    43: "Seek help, discuss with classmates, and adopt different learning methods to overcome learning difficulties.",
    44: "Anki is a useful flashcard app that can help you study more efficiently.",
    45: "Environmental protection is crucial; we can contribute by reducing waste, conserving energy, etc.",
    46: "Build trust, communicate clearly, avoid impulsiveness and extreme language for effective communication.",
    47: "As a program, I don't have personal expectations, but I hope to continue providing assistance to users.",
    48: "Establish trust, clarify roles, communicate regularly, and work together to achieve team goals.",
    49: "Making new friends, trying local cuisine, and visiting local attractions are unforgettable travel memories.",
    50: "There are new discoveries in the field of science every day; you can check science news websites for the latest information.",
    51: "I'm a program and don't have preferences, but many people enjoy various cuisines like Italian, Chinese, or Mexican.",
    52: "There are many productivity tools available; it depends on your needs. Trello, Notion, and Todoist are popular choices.",
    53: "Stay organized, eliminate distractions, take breaks, and set specific goals to stay focused while working or studying.",
    54: "One memorable quote is 'May the Force be with you' from Star Wars.",
    55: "Prioritize tasks, delegate when possible, and practice time-blocking to handle a busy schedule effectively.",
    56: "Create a to-do list, set realistic deadlines, and use techniques like the Pomodoro method for effective time management.",
    57: "Hobbies are important for personal well-being. They provide relaxation, creativity, and a break from routine.",
    58: "The documentary 'Planet Earth II' is visually stunning and offers insights into the natural world.",
    59: "Stay focused on your goals, break them into smaller tasks, and celebrate small achievements to stay motivated.",
    60: "Apps like Headspace and Calm are popular for meditation; find one that suits your preferences.",
    61: "Social media has both positive and negative impacts. It connects people but can also contribute to information overload.",
    62: "Practice active recall, use mnemonic devices, and maintain a healthy lifestyle for better memory retention.",
    63: "Start by choosing a niche, creating valuable content, and engaging with your audience to grow a blog.",
    64: "Consistent practice, take lessons if possible, and enjoy the learning process to master a musical instrument.",
    65: "Both indoor and outdoor activities have their charm. It depends on personal preferences and the weather.",
    66: "Communication is key. Practice active listening, express thoughts clearly, and be open to compromise in relationships.",
    67: "The most interesting place I've ever visited is the Great Barrier Reef with its breathtaking underwater beauty.",
    68: "Effective communication involves listening, empathy, and clarity in conveying messages.",
    69: "Practice regularly, visualize success, and focus on the message rather than personal performance to overcome anxiety.",
    70: "I don't have a morning routine, but many find starting the day with exercise or meditation beneficial.",
    71: "Manage workload, take breaks, and seek support from colleagues or supervisors to handle stress at work.",
    72: "Podcasts like 'TED Talks Daily' and 'How I Built This' offer insightful and inspiring content.",
    73: "Encourage reading from a young age, provide diverse books, and make it a fun and interactive experience.",
    74: "Learn by doing, build projects, and immerse yourself in coding communities to master a new programming language.",
    75: "Technology plays a significant role in education, providing accessibility and innovative learning methods.",
    76: "Set SMART goals, break them into smaller tasks, and track progress regularly for effective goal achievement.",
    77: "My favorite type of exercise is virtual jumping through the hoops of code.",
    78: "Stay open-minded, build relationships, and be proactive to succeed in a new job.",
    79: "Relaxation methods vary; some prefer reading, others meditation or a walk in nature.",
    80: "I don't play board games or cards, but classics like Chess and Poker are popular choices.",
    81: "Align your skills and interests with potential careers, seek advice, and explore opportunities to find your passion.",
    82: "Foster open communication, encourage collaboration, and recognize team achievements for a positive team culture.",
    83: "Save money by budgeting, cutting unnecessary expenses, and investing wisely for future financial goals.",
    84: "Overcoming writer's block involves taking breaks, changing surroundings, and writing without self-judgment.",
    85: "I don't have personal preferences, but 'Dune' and 'Neuromancer' are acclaimed science fiction books.",
    86: "Self-care is essential for overall well-being. Prioritize rest, relaxation, and activities that bring joy.",
    87: "For a weekend getaway with friends, consider a cabin retreat, beach trip, or exploring a nearby city.",
    88: "Handle criticism constructively by seeking feedback, learning from it, and focusing on personal growth.",
    89: "I don't have preferences, but popular desserts include chocolate cake, apple pie, and ice cream.",
    90: "Setting New Year's resolutions can be beneficial if they are realistic, specific, and achievable.",
    91: "Break a bad habit by identifying triggers, replacing with positive habits, and seeking support when needed.",
    92: "Effective team collaboration in remote work involves clear communication, collaborative tools, and regular check-ins.",
    93: "I don't have personal memories, but many cherish childhood memories of family, friends, and adventures.",
    94: "Manage information overload by prioritizing, setting boundaries, and using tools for efficient information consumption.",
    95: "Documentaries like 'The Civil War' and 'The World at War' offer in-depth insights into historical events.",
    96: "Stay organized with calendars, to-do lists, and decluttering physical and digital spaces.",
    97: "Prepare for a job interview by researching the company, practicing common questions, and showcasing your skills.",
    98: "Encourage creativity in a team by fostering an open-minded environment, recognizing diverse talents, and encouraging idea-sharing.",
    99: "Spending a lazy Sunday can involve reading, watching movies, or enjoying a leisurely brunch.",
    100: "Explore lesser-known destinations like Bhutan, Madagascar, or Iceland for unique travel experiences."
}

questions_dict_chinese = {
    1: "什么是集成电路制造工艺？",
    2: "集成电路制造工艺的主要步骤有哪些？",
    3: "什么是光刻工艺？",
    4: "什么是刻蚀工艺？",
    5: "什么是沉积工艺？",
    6: "什么是离子注入工艺？",
    7: "为什么需要退火工艺？",
    8: "集成电路制造工艺中的工艺参数如何选择？",
    9: "集成电路制造中的工艺控制如何实现？",
    10: "如何处理制造过程中的缺陷？",
    11: "工艺创新在集成电路制造中的作用是什么？",
    12: "集成电路制造中的材料选择有哪些考虑因素？",
    13: "如何评估和优化集成电路制造工艺？",
    14: "未来集成电路制造工艺发展的趋势是什么？",
    15: "推荐一个好用的手机应用。",
    16: "有什么激励人心的名言分享吗？",
    17: "如何克服拖延症？",
    18: "有什么好的周末度假建议吗？",
    19: "如何学习一门新语言？",
    20: "有什么健身建议吗？",
    21: "你对人工智能的未来有什么看法？",
    22: "如何正确使用社交媒体？",
    23: "有什么推荐的早餐？",
    24: "最喜欢的季节是哪一个？",
    25: "如何保持积极的心态？",
    26: "如何正确利用时间？",
    27: "有什么推荐的健康零食？",
    28: "你喜欢看电影吗？",
    29: "如何养成阅读的习惯？",
    30: "有什么旅行的建议吗？",
    31: "如何应对工作压力？",
    32: "茶还是咖啡？",
    33: "如何培养创造力？",
    34: "有没有好的餐厅推荐？",
    35: "有没有购物建议？",
    36: "如何克服对公共演讲的恐惧？",
    37: "你有没有最喜欢的电影类型？",
    38: "如何保持社交生活的平衡？",
    39: "有没有推荐的健身活动？",
    40: "如何学会放松？",
    41: "你参加过游戏比赛吗？",
    42: "推荐一本励志书。",
    43: "如何克服学习困难？",
    44: "有没有好的学习工具推荐？",
    45: "你对环保有什么看法？",
    46: "如何保持良好的沟通？",
    47: "你对未来的期望是什么？",
    48: "如何在工作中建立良好的团队合作？",
    49: "有没有旅行回忆分享？",
    50: "最近有什么有趣的科学发现吗？",
    51: "你最喜欢的美食是什么？",
    52: "有关提高工作效率的工具有什么推荐吗？",
    53: "在工作或学习时如何保持专注？",
    54: "你有没有电影中喜欢的经典台词？",
    55: "如何处理繁忙的日程安排？",
    56: "关于有效的时间管理，有什么建议？",
    57: "对于爱好的重要性，你有什么看法？",
    58: "推荐一部值得观看的纪录片。",
    59: "在困难时如何保持动力？",
    60: "你有没有喜欢的冥想应用？",
    61: "你对社交媒体对社会的影响持什么态度？",
    62: "关于提高记忆力，有什么建议？",
    63: "如何开始博客并吸引读者？",
    64: "学习乐器的最佳方法是什么？",
    65: "你更喜欢室内活动还是户外活动？",
    66: "处理感情冲突的建议有哪些？",
    67: "你去过的最有趣的地方是哪里？",
    68: "如何培养有效的沟通技巧？",
    69: "对于公共演讲焦虑，有没有推荐的策略？",
    70: "你有没有特定的早晨习惯？",
    71: "处理工作压力的方法是什么？",
    72: "有没有推荐的播客？",
    73: "如何培养孩子阅读习惯？",
    74: "学习新编程语言的最佳途径是什么？",
    75: "对于科技在教育中的作用，你有什么想法？",
    76: "如何设定并有效实现个人目标？",
    77: "你最喜欢的运动方式是什么？",
    78: "对于刚开始新工作的人，有什么建议？",
    79: "你偏好的放松方法是什么？",
    80: "你有没有喜欢的棋盘游戏或纸牌游戏？",
    81: "如何选择与你的激情相符的职业？",
    82: "在工作中保持积极的团队文化有什么建议？",
    83: "为未来目标存钱的最佳方法是什么？",
    84: "在写作时如何克服写作障碍？",
    85: "你有没有喜欢的科幻小说？",
    86: "对于自我保健的重要性，你有什么看法？",
    87: "与朋友一起度过周末的建议有哪些？",
    88: "如何以建设性的方式处理批评？",
    89: "你最喜欢的甜点是什么？",
    90: "你相信制定新年计划吗？",
    91: "如何改掉坏习惯并养成好习惯？",
    92: "在远程工作环境中有效团队协作的建议有哪些？",
    93: "你童年的最喜欢的记忆是什么？",
    94: "如何应对数字时代的信息过载？",
    95: "你有没有喜欢的历史纪录片？",
    96: "在日常生活中保持有序的方法是什么？",
    97: "对于成功的求职面试，有什么建议？",
    98: "如何在团队环境中激发创造力？",
    99: "你最喜欢的度过悠闲周日的方式是什么？",
    100: "你有没有喜欢的独特旅行目的地？"
}

answers_dict_chinese = {
    1: "集成电路制造工艺是指将电子器件（如晶体管、电容器等）集成到硅片上，并通过一系列的加工步骤形成完整的集成电路芯片的过程。",
    2: "主要步骤包括晶圆制备、清洗、光刻、刻蚀、沉积、离子注入、退火和切割等。",
    3: "光刻工艺是将芯片图案投射到硅片上的过程，通过光刻胶和光罩的结合，确定芯片上不同区域的图案。",
    4: "刻蚀工艺是将硅片上不需要的部分材料去除的过程，常用于形成沟槽、孔洞等结构。",
    5: "沉积工艺是将新的材料沉积到硅片表面的过程，常用于形成金属线、绝缘层等结构。",
    6: "离子注入工艺是将离子注入硅片表面的过程，通过控制离子注入的能量和剂量，改变硅片材料的电学性质。",
    7: "退火工艺是通过加热硅片来改变其内部结构和性能，使其更适合集成电路的要求。",
    8: "工艺参数的选择需要考虑芯片性能要求、成本和制造设备能力等因素，通常需要进行大量实验和优化。",
    9: "工艺控制需要通过严格的工艺规范、实时监测和反馈控制等手段来确保每一步工艺都符合要求。",
    10: "处理缺陷通常需要进行缺陷分析，找出缺陷产生的原因，并采取相应的措施进行修复或调整工艺。",
    11: "工艺创新可以带来更高的生产效率、更低的成本和更好的产品性能，是推动集成电路制造技术进步的重要驱动力。",
    12: "材料选择需要考虑其电学性能、机械性能、耐高温性能等因素，同时还要考虑成本和可靠性等因素。",
    13: "评估和优化工艺需要通过实验、模拟和数据分析等手段，找出最优的工艺参数组合以满足设计要求。",
    14: "未来的发展趋势包括工艺尺寸的进一步缩小、新材料的应用，以及工艺自动化和智能化的发展方向。",
    15: "Evernote是一个用户友好的记事应用，可以帮助你轻松组织信息。",
    16: "‘成功不是将来的事情；它是每一天的事情。’",
    17: "将任务分解为较小的步骤，制定明确的计划，逐步完成任务以克服拖延症。",
    18: "考虑在附近的公园散步，参加社交活动，或尝试新餐馆度过愉快的周末。",
    19: "每天练习，使用语言学习应用，与母语者进行交流以提高语言技能。",
    20: "结合有氧运动和力量训练，定期锻炼，保持整体身体健康。",
    21: "人工智能的未来可能涉及更广泛的应用，但道德和隐私问题需要谨慎考虑。",
    22: "保持理性，管理隐私设置，避免过度使用，并选择性关注有价值的内容以获得积极体验。",
    23: "吃一个均衡的早餐，包括蛋白质、蔬菜和水果，为一天提供必要的能量。",
    24: "我不能感知季节，但许多人喜欢春天的温暖和盛开的花朵。",
    25: "培养感恩之心，关注积极的事物，并与乐观的人交往以保持积极心态。",
    26: "制定合理的时间表，设置优先级，并明智地分配时间以避免浪费时间。",
    27: "坚果、水果、酸奶等是健康的零食选择。",
    28: "我不能体验娱乐，但我可以为你推荐好电影。",
    29: "定期阅读，选择感兴趣的书籍，并逐渐培养对阅读的热情。",
    30: "规划行程，尝试当地美食，并与当地人互动，使您的旅行更加丰富多彩。",
    31: "学会合理分配任务，定期休息，并找到缓解压力的方法，如锻炼或冥想。",
    32: "我没有口味偏好，但茶和咖啡都有各自的魅力；根据个人口味选择。",
    33: "保持好奇心，尝试新事物，并思考问题的不同解决方案以培养创造力。",
    34: "这取决于你所在的城市，但你可以尝试搜索本地食品评论以找到受欢迎的餐厅。",
    35: "制定购物清单，留意折扣和促销，避免冲动购物以理性购物。",
    36: "练习公共演讲，深呼吸放松，专注于分享有价值的信息以克服恐惧。",
    37: "我没有个人喜好，但科幻、喜剧和戏剧是受欢迎的电影类型。",
    38: "定期与朋友社交，保持联系，但也要分配一些个人时间。",
    39: "跑步、瑜伽、游泳等是有效的健身活动；选择适合你的那种。",
    40: "尝试冥想、听音乐、阅读等，找到适合你的放松方法。",
    41: "我是一个程序，无法参加游戏比赛，但我可以提供游戏推荐。",
    42: "《牧羊少年奇幻之旅》是一本广受喜爱的励志书，值得一读。",
    43: "寻求帮助，与同学讨论，并采用不同的学习方法以克服学习困难。",
    44: "Anki是一个有用的单词卡应用，可以帮助你更有效地学习。",
    45: "环保至关重要；我们可以通过减少废物、节约能源等方式做出贡献。",
    46: "建立信任，清晰沟通，避免冲动和极端语言，以实现有效沟通。",
    47: "作为一个程序，我没有个人期望，但我希望能继续为用户提供帮助。",
    48: "建立信任，明确角色，定期沟通，并共同努力实现团队目标。",
    49: "结交新朋友，品尝当地美食，并参观当地景点是难忘的旅行回忆。",
    50: "科学领域每天都有新的发现；你可以查看科学新闻网站获取最新信息。",
    51: "我是一个程序，没有偏好，但很多人喜欢意大利菜、中餐或墨西哥菜等各种美食。",
    52: "有许多生产力工具可用；这取决于您的需求。Trello、Notion和Todoist是受欢迎的选择。",
    53: "保持组织，消除干扰，休息一下，并设定具体目标以保持工作或学习的专注。",
    54: "一句令人难忘的台词是《星球大战》中的‘愿原力与你同在’。",
    55: "优先处理任务，可能时分派任务，并采用时间分块等方法有效处理繁忙的日程。",
    56: "创建待办事项列表，设定实际期限，并使用番茄工作法等方法进行有效的时间管理。",
    57: "爱好对个人福祉很重要。它们提供放松、创造力，并打破例行公事。",
    58: "纪录片《地球脉动 II》视觉上令人惊叹，并提供对自然界的深刻洞察。",
    59: "保持专注于目标，将其分解为较小的任务，并庆祝小的成就以保持动力。",
    60: "诸如Headspace和Calm之类的应用在冥想方面很受欢迎；找到适合您的那个。",
    61: "社交媒体既有积极的影响，又可能导致信息过载。",
    62: "积极回忆，使用记忆辅助工具，并保持健康的生活方式以提高记忆力。",
    63: "从选择一个利基开始，创造有价值的内容，并与观众互动以发展博客。",
    64: "保持一贯的练习，如果可能的话参加课程，并享受学习过程以掌握乐器。",
    65: "室内和户外活动都有吸引力。这取决于个人的喜好和天气。",
    66: "沟通是关键。在关系中练习积极倾听，清晰表达思想，并愿意妥协。",
    67: "我曾经访问过的最有趣的地方是大堡礁，那里有令人惊叹的水下美景。",
    68: "有效的沟通包括倾听、共鸣和清晰传达信息。",
    69: "定期练习，形象化成功，并专注于信息而不是个人表现，以克服焦虑。",
    70: "我没有早晨的例行程序，但很多人发现以锻炼或冥想开始一天是有益的。",
    71: "管理工作量，休息一下，并向同事或主管寻求支持以应对工作压力。",
    72: "像‘TED演讲日报’和‘我是如何创办这个的’等播客提供深刻而鼓舞人心的内容。",
    73: "从小培养阅读习惯，提供多样化的书籍，并使之成为有趣而互动的体验。",
    74: "通过实践学习，制作项目，并沉浸于编码社区中，以掌握新的编程语言。",
    75: "技术在教育中发挥着重要作用，提供了便捷的学习方法和创新的教育手段。",
    76: "设定SMART目标，将其分解为较小的任务，并定期跟踪进度以有效实现目标。",
    77: "我最喜欢的运动方式是通过代码的篮圈进行虚拟跳跃。",
    78: "保持开放的心态，建立关系，并积极进取以在新工作中取得成功。",
    79: "放松的方法各不相同；有些人喜欢阅读，其他人则喜欢冥想或在自然中散步。",
    80: "我不玩棋盘游戏或纸牌，但象棋和扑克等经典游戏很受欢迎。",
    81: "将技能和兴趣与潜在的职业相结合，寻求建议，并探索机会以找到自己的激情。",
    82: "促进开放的沟通，鼓励合作，并认可团队的成就，以营造积极的团队文化。",
    83: "通过制定预算，削减不必要的开支，并明智投资为未来的财务目标储蓄。",
    84: "克服写作困境涉及休息一下，改变环境，并在不自我批判的情况下写作。",
    85: "我没有个人喜好，但《沙丘》和《神经漫游者》是备受推崇的科幻小说。",
    86: "自我关怀对整体福祉至关重要。优先考虑休息、放松和带来快乐的活动。",
    87: "与朋友一起度周末，考虑山中小屋、海滩之旅或探索附近的城市。",
    88: "以建设性的方式处理批评，寻求反馈，从中学习，并专注于个人成长。",
    89: "我没有偏好，但受欢迎的甜点包括巧克力蛋糕、苹果派和冰淇淋。",
    90: "设定新年的决心如果具体、切实可行，是有益的。",
    91: "通过识别触发因素、用积极习惯替代坏习惯，并在需要时寻求支持来改变坏习惯。",
    92: "在远程工作环境中，实现有效的团队协作涉及清晰的沟通、协作工具和定期检查。",
    93: "我没有个人记忆，但许多人珍视童年的家庭、朋友和冒险记忆。",
    94: "通过优先考虑、设定界限和使用工具，有效处理数字时代的信息过载。",
    95: "纪录片《内战》和《世界大战》深入剖析历史事件。",
    96: "通过日历、待办事项列表和整理物理和数字空间保持组织。",
    97: "为面试做好准备，研究公司、练习常见问题，并展示您的技能。",
    98: "通过营造开明的环境、认可多样化的才能，并鼓励分享创意来激发团队中的创造力。",
    99: "度过悠闲的周日可以包括阅读、观看电影或享受悠闲的早午餐。",
    100: "探索鲜为人知的目的地，如不丹、马达加斯加或冰岛，获得独特的旅行体验。"
}

anomaly_questions = [
    'Are there any anomalies in the image?',
    'Are there any defects in the image?',
    'Is there any defect in the image?',
    'Is there any anomaly in the image?',
    'Do you observe any irregularities in the image?',
    'Are there any discrepancies in the image?',
    'Can you identify any aberrations in the image?',
    'Do you notice any abnormalities in the image?',
    'Are there any inconsistencies in the image?',
    'Is there any deviance in the image?',
    'Are there any anomalies present in the image?',
    'Do you perceive any faults in the image?',
    'Can you spot any atypical elements in the image?',
    'Are there any variations from the norm in the image?',
    'Do you see any irregular occurrences in the image?',
    'Is there any departure from the standard in the image?',
    'Can you detect any nonconformities in the image?',
    'Are there any divergences in the image?',
    'Do you identify any incongruities in the image?',
    'Is there any departure from expectations in the image?',
    'Are there any aberrant features in the image?',
    'Can you pinpoint any anomalies in the image?',
    'Do you discern any atypical aspects in the image?',
    'Are there any unusual elements in the image?',
    'How many defect regions are present in this image?',
    'How many anomalies are there in this image?',
    'What is the number of anomaly regions in this image?',
    'How many anomaly regions can be detected in this image?',
    'How many defect locations are present in this image?',
    'What is the number of anomaly points in the image?',
    'Where are the defects located in the image?',
    'What are the locations of the defect regions in this image?',
    'Where are the anomalies in this image?',
    'In which areas are the defects distributed in this image?',
    'Where exactly are the defects in the image?',
    'Where are the defect regions in this image?'
]

anomaly_questions_cn = [
    "图像中是否存在任何异常？",
    "图像中是否存在任何缺陷？",
    "图像中是否有任何缺陷？",
    "图像中是否存在任何异常？",
    "你是否观察到图像中的任何不规则之处？",
    "你能否识别出图像中的任何异常现象？",
    "你是否注意到图像中的任何异常情况？",
    "图像中是否存在任何不一致之处？",
    "图像中是否存在任何异常情况？",
    "你是否察觉到图像中的任何缺陷？",
    "你能否发现图像中的任何非典型元素？",
    "图像中是否存在与常规不同的地方？",
    "你是否在图像中看到任何不规则的事件？",
    "图像中是否存在与标准不符的地方？",
    "图像中是否存在任何分歧？",
    "你是否辨别出图像中的任何不一致之处？",
    "图像中是否存在与预期不符的地方？",
    "图像中是否存在任何异常特征？",
    "你能否准确定位图像中的任何异常？",
    "图像中是否存在任何不寻常的元素？",
    "这张图像包含多少个缺陷区域？",
    "在这张图像中，有多少处异常？",
    "这幅图中异常区域的数量是多少？",
    "这张图像中可以检测到多少个异常区域？",
    "该图像中存在多少个缺陷位置？",
    "图像中的异常点数量是多少？",
    "图像中的缺陷在图像中的什么位置？",
    "这张图像中的缺陷区域位于哪些地方？",
    "缺陷在这幅图像的什么位置？",
    "该图像中的缺陷分布在哪些区域？",
    "图像中的缺陷具体在哪些位置？",
    "这张图像的缺陷区域在哪里？"
]

normal_answers = [
    'No, there is no anomaly in the image.',
    'No, there is no defect in the image.',
    'No, there are no anomalies in the image.',
    'No, there are no defects in the image.',
    "No, this is a photo of {} without any anomalies.",
    "No, this is a photo of {} without any defects.",
    'No, there is no irregularity in the image.',
    'No, there is no imperfection in the image.',
    'No, there are no abnormalities in the image.',
    'No, there are no blemishes in the image.',
    'No, this is a photo of {} without any irregularities.',
    'No, this is a photo of {} without any imperfections.',
    'No, there are no irregularities present in the image.',
    'No, there are no flaws in the image.',
    'No, there are no anomalies detected in the image.',
    'No, there are no defects to be found in the image.',
    'No, this is a photo of {} with no irregularities.',
    'No, this is a photo of {} with no imperfections.',
    'No, the image is free from irregularities.',
    'No, the image does not exhibit any flaws.',
    'No, there are no abnormalities observed in the image.',
    'No, there are no blemishes spotted in the image.',
    'No, this image of {} shows no irregularities.',
    'No, this image of {} displays no imperfections.',
    'No, there are no irregularities visible in the image.',
    'No, there are no defects evident in the image.'
]

normal_answers_cn = [
    "不，图像中没有任何异常。",
    "不，图像中没有任何缺陷。",
    "不，图像中没有任何异常现象。",
    "不，图像中没有任何缺陷。",
    "不，这是一张没有任何异常的{}照片。",
    "不，这是一张没有任何缺陷的{}照片。",
    "不，图像中没有任何不规则之处。",
    "不，图像中没有任何瑕疵。",
    "不，图像中没有任何异常情况。",
    "不，图像中没有任何瑕疵。",
    "不，这是一张没有任何不规则之处的{}照片。",
    "不，这是一张没有任何瑕疵的{}照片。",
    "不，图像中没有任何不规则现象。",
    "不，图像中没有任何瑕疵。",
    "不，图像中没有任何异常被检测出。",
    "不，图像中没有任何缺陷可寻找。",
    "不，这是一张没有任何不规则之处的{}照片。",
    "不，这是一张没有任何瑕疵的{}照片。",
    "不，图像中没有任何不规则之处。",
    "不，图像中没有任何瑕疵。",
    "不，图像中没有任何异常现象。",
    "不，图像中没有任何瑕疵。",
    "不，这张{}的照片没有任何不规则之处。",
    "不，这张{}的照片没有任何瑕疵。",
    "不，图像中没有任何不规则之处可见。",
    "不，图像中没有任何可见瑕疵。"
]

detail_questions = [
    "What's the anomaly?",
    "What's the defect?",
    "What are the anomalies?",
    "What are the defects?",
    "Why you think so?",
    "What's the irregularity?"
    "What's the flaw?",
    "What are the irregularities?",
    "What are the flaws?",
    "Can you identify the anomaly?",
    "Could you point out the defect?",
    "Do you see any anomalies?",
    "Do you notice any defects?",
    "What's considered anomalous?",
    "What's deemed as a defect?",
    "Can you detect any anomalies?",
    "Can you spot any defects?",
    "What constitutes an anomaly?",
    "What falls under the category of defects?",
    "What's regarded as an anomaly?",
    "What's categorized as a defect?",
    "What anomalies are present?",
    "What defects have been identified?",
    "What kind of anomalies are we looking at?",
    "What types of defects are visible?",
]

detail_questions_cn = [
    "异常部分是什么？",
    "缺陷是什么？",
    "有哪些异常？",
    "有哪些缺陷？",
    "你为什么这么认为？",
    "有什么不规则之处吗？",
    "有什么缺陷吗？",
    "有哪些不规则之处？",
    "有哪些缺陷？",
    "你能识别出异常吗？",
    "你能指出缺陷吗？",
    "你看到了任何异常吗？",
    "你注意到了任何缺陷吗？",
    "什么被认为是异常的？",
    "什么被视为缺陷？",
    "你能检测出任何异常吗？",
    "你能发现任何缺陷吗？",
    "什么构成了异常？",
    "什么属于缺陷的范畴？",
    "什么被看作是异常？",
    "什么被归类为缺陷？",
    "有什么异常存在吗？",
    "有哪些缺陷被发现了？",
    "有哪些类型的缺陷是可见的？"
]

PCB_names = [
    'printed wiring board',
    'circuit card',
    'electronic board',
    'PCB assembly',
    'circuitry panel',
    'circuit substrate',
    'wiring substrate',
    'circuit laminate',
    'electronic substrate',
    'board with printed circuits',
    'PCB layout',
    'circuit interconnect board',
    'electrical board',
    'integrated circuit board',
    'printed wiring assembly',
    'PCB design',
    'printed electronic board',
    'conductor board',
    'printed circuitry card',
    'electronics motherboard'
]

PCB_names_cn = [
    "印刷线路板",
    "电路板",
    "PCB组件",
    "电路板面",
    "电路基板",
    "布线基板",
    "电路层压板",
    "电子基板",
    "带印刷电路的板子",
    "PCB",
    "电路互连板",
    "电气板",
    "集成电路板",
    "印刷布线组件",
    "印刷电路板",
    "导体板",
    "印刷电路卡",
    "电子主板"
]

Road_names = [
    'pavement',
    'concrete',
    'road',
    'sideroad',
    'concrete road',
    'roadway',
    'surface',
    'street',
    'wall',
    'concrete surfacce',
    'concrete wall'
]

Road_names_cn = [
    "人行道",
    "混凝土",
    "道路",
    "小路",
    "混凝土路",
    "道路",
    "路面",
    "水泥路表面",
    "墙面"
]


def get_class_name(name):
    global PCB_names
    if name == 'candle':
        return 'candles'
    elif 'macaroni' in name:
        return 'macaronis'
    elif 'pcb' in name:
        return random.choice(PCB_names)
    elif name == 'road':
        return random.choice(Road_names)
    else:
        return name.replace('_', " ")


# TODO: Finish This
def get_class_name_cn(name):
    global PCB_names
    if name in Chinese_class_names.keys():
        return random.choice(Chinese_class_names[name])
    elif 'pcb' in name:
        return random.choice(PCB_names_cn)
    elif name == 'road':
        return random.choice(Road_names_cn)
    else:
        return random.choice(['元件', '元器件', '样品', '样本'])


def format_position(position):
    ret = ""
    for i in range(len(position)):
        if i == 0:
            ret += position[i]
        else:
            if i != len(position) - 1:
                ret += ", "
                ret += position[i]
            else:
                ret += " and " + position[i]

    return ret


def format_position_cn(position):
    ret = ""
    for i in range(len(position)):
        if i == 0:
            ret += Chinese_position[position[i]]
        else:
            if i != len(position) - 1:
                ret += "，"
                ret += Chinese_position[position[i]]
            else:
                ret += "和" + Chinese_position[position[i]]

    return ret


class SupervisedDataset(Dataset):
    def __init__(self, root_dir: str):
        self.root_dir = root_dir
        self.resize = transforms.Resize(
            (224, 224), interpolation=transforms.InterpolationMode.BICUBIC
        )

        self.norm_transform = transforms.Compose(
            [
                transforms.ToTensor(),
                transforms.Normalize(
                    mean=(0.48145466, 0.4578275, 0.40821073),
                    std=(0.26862954, 0.26130258, 0.27577711),
                ),
            ]
        )

        self.paths = []
        for root, dirs, files in os.walk(root_dir):
            for file in files:
                file_path = os.path.join(root, file)
                if ('masks' not in file_path and 'ground_truth' not in file_path) and (
                        'png' in file_path or 'JPG' in file_path or 'JPEG' in file_path or 'jpg' in file_path):
                    self.paths.append(file_path)

    def __len__(self):
        return len(self.paths)

    def __getitem__(self, index):

        img_path = self.paths[index]
        img = self.resize(Image.open(img_path).convert('RGB'))
        if 'SEM' in img_path or 'visa' in img_path or 'mvtec_loco_anomaly_detection' in img_path:
            class_name = img_path.split('/')[-4]
        elif 'road' in img_path:
            class_name = 'road'

        centers = []

        if 'good' not in img_path:
            if 'SEM' in img_path:
                mask_path = img_path.replace('test', 'ground_truth')
                mask_path = mask_path.replace('.png', '_mask.png')
            elif 'visa' in img_path:
                mask_path = img_path.replace('test', 'ground_truth')
                mask_path = mask_path.replace('.JPG', '.png')
            elif 'mvtec_loco_anomaly_detection' in img_path:
                mask_path = img_path.replace('test', 'ground_truth')
                mask_path = mask_path.replace('.png', '/000.png')
            elif 'crack_road' in img_path:
                mask_path = img_path.replace('images', 'masks')
                mask_path = mask_path.replace('.jpg', '.png')
            elif 'iva_road' in img_path:
                mask_path = img_path.replace('images', 'masks')
                mask_path = mask_path.replace('.jpg', '.png')
            elif 'Magnetic-Tile-Defect' in img_path:
                mask_path = img_path.replace('Imgs', 'masks')
                mask_path = mask_path.replace('.jpg', '.png')
            mask = cv2.imread(mask_path, cv2.IMREAD_GRAYSCALE)
            mask = cv2.resize(mask, (224, 224))
            centers = find_contours(mask)
            mask = transforms.ToTensor()(mask)
        else:
            mask = torch.zeros((1, 224, 224))

        img = self.norm_transform(img)

        position = []
        if len(centers) > 0:
            for center in centers:
                center_y = center[0] / 224
                center_x = center[1] / 224

                if center_x <= 1 / 3 and center_y <= 1 / 3:
                    position.append('top left')
                elif center_x <= 1 / 3 and center_y > 1 / 3 and center_y <= 2 / 3:
                    position.append('top')
                elif center_x <= 1 / 3 and center_y > 2 / 3:
                    position.append('top right')

                elif center_x <= 2 / 3 and center_y <= 1 / 3:
                    position.append('left')
                elif center_x <= 2 / 3 and center_y > 1 / 3 and center_y <= 2 / 3:
                    position.append('center')
                elif center_x <= 2 / 3 and center_y > 2 / 3:
                    position.append('right')

                elif center_y <= 1 / 3:
                    position.append('bottom left')
                elif center_y > 1 / 3 and center_y <= 2 / 3:
                    position.append('bottom')
                elif center_y > 2 / 3:
                    position.append('bottom right')

            position = list(set(position))

        conversation = []
        # conversation1 = []

        Use_chinese = random.randint(0, 1) == 0

        m = random.randint(0, 2)
        # m=0

        if m!=2:
            r = random.randint(0, 2)
            if not Use_chinese:
                if r == 0 and 'mvtec_loco_anomaly_detection' not in img_path:
                    conversation.append({"from": "human", "value": random.choice(class_questions)})
                    if class_name not in MULTI_CLASS:
                        conversation.append(
                            {"from": "gpt", "value": random.choice(single_answers).format(get_class_name(class_name))})
                    else:
                        conversation.append(
                            {"from": "gpt", "value": random.choice(multi_answers).format(get_class_name(class_name))})
                # if r == 0 and 'mvtec_loco_anomaly_detection' not in img_path:
                    # conversation.append({"from": "human", "value": random.choice(class_questions_what).format('SEM')})
                    # conversation.append(
                    #         {"from": "gpt", "value": random.choice(single_answers_what['SEM'])})
            else:
                if r == 0 and 'mvtec_loco_anomaly_detection' not in img_path:
                    conversation.append({"from": "human", "value": random.choice(class_questions_cn)})
                    if class_name not in MULTI_CLASS:
                        conversation.append({"from": "gpt", "value": random.choice(single_answers_cn).format(
                            get_class_name_cn(class_name))})
                    else:
                        conversation.append(
                            {"from": "gpt", "value": random.choice(multi_answers_cn).format(get_class_name_cn(class_name))})
                # if r == 0 and 'mvtec_loco_anomaly_detection' not in img_path:
                    # conversation.append({"from": "human", "value": random.choice(class_questions__what_cn).format('SEM')})
                    # conversation.append(
                    #         {"from": "gpt", "value": random.choice(single_answers_what_cn['SEM'])})

            if not Use_chinese:
                conversation.append({"from": "human", "value": random.choice(anomaly_questions)})
                if len(centers) == 0:
                    conversation.append(
                        {"from": "gpt", "value": random.choice(normal_answers).format(get_class_name(class_name))})
                if len(centers) == 1:
                    abnormal_describe = "Yes, there is {} in the image, at the {} of the image.".format(
                        random.choice(['an anomaly', 'a defect']), position[0])
                    conversation.append({"from": "gpt", "value": abnormal_describe})
                elif len(centers) > 1:
                    if class_name != 'road':
                        abnormal_describe = "Yes, there are {} anomalies in the image, they are at the {} of the image.".format(
                            str(len(centers)), format_position(position))
                    else:
                        abnormal_describe = "Yes, there is {} in the image.".format(
                            random.choice(['an anomaly', 'a defect']))
                    conversation.append({"from": "gpt", "value": abnormal_describe})
            else:
                conversation.append({"from": "human", "value": random.choice(anomaly_questions_cn)})
                if len(centers) == 0:
                    conversation.append(
                        {"from": "gpt", "value": random.choice(normal_answers_cn).format(get_class_name_cn(class_name))})
                if len(centers) == 1:
                    abnormal_describe = "是的，图中有1个{}， 在图像的{}。".format(random.choice(['异常', '缺陷']),
                                                                            format_position_cn(position))
                    conversation.append({"from": "gpt", "value": abnormal_describe})
                elif len(centers) > 1:
                    if class_name != 'road':
                        abnormal_describe = "是的，图中有{}个异常, 在图像的{}.".format(str(len(centers)),
                                                                                    format_position_cn(position))
                    else:
                        abnormal_describe = "是的，图中有1个异常。"
                    conversation.append({"from": "gpt", "value": abnormal_describe})

            if 'good' not in img_path and 'SEM' in img_path:
                anomaly_detail = img_path.split('/')[-2]
                if not Use_chinese:
                    conversation.append({"from": "human", "value": random.choice(detail_questions)})
                else:
                    conversation.append({"from": "human", "value": random.choice(detail_questions_cn)})

                detail_answer = ''
                detail_answer_cn = ''
                be = 'is' if len(centers) == 1 else 'are'
                num = 'a' if len(centers) == 1 else str(len(centers))
                p = format_position(position)
                p_cn = format_position_cn(position)
                s = '' if len(centers) == 1 else 's'
                es = '' if len(centers) == 1 else 'es'

                flag = 1
                if class_name == 'SEM-image':
                    if anomaly_detail == 'hole':
                        detail_answer = 'There {} {} hole{} at the {} of the {}.'.format(be, num, s, p,
                                                                                        get_class_name(class_name))
                        detail_answer_cn = '图像中的{}有{}个洞的地方，在图像的{}。'.format(get_class_name_cn(class_name), num,
                                                                                        p_cn)
                    elif anomaly_detail == 'infilm':
                        detail_answer = 'There {} {} infilm{} at the {} of the {}.'.format(be, num, s, p,
                                                                                        get_class_name(class_name))
                        detail_answer_cn = '图像中的{}有{}块渗透的地方，在图像的{}。'.format(get_class_name_cn(class_name),
                                                                                        num, p_cn)
                    elif anomaly_detail == 'particle':
                        detail_answer = 'There {} {} particle{} at the {} of the {}.'.format(be, num, s, p,
                                                                                            get_class_name(class_name))
                        detail_answer_cn = '图像中的{}有{}个颗粒的地方，在图像的{}。'.format(get_class_name_cn(class_name),
                                                                                        num, p_cn)
                    elif anomaly_detail == 'pattern_deform':
                        detail_answer = 'The pattern of {} is deform, at the {}.'.format(get_class_name(class_name), p)
                        detail_answer_cn = '图像中{}的样式变形了，在图像的{}。'.format(get_class_name_cn(class_name), p_cn)
                    elif anomaly_detail == 'puddle':
                        detail_answer = 'There is puddle at the {} of the {}.'.format(p, get_class_name(class_name))
                        detail_answer_cn = '{}的图像中有{}滩水坑，在图像的{}。'.format(get_class_name_cn(class_name), num,
                                                                                    p_cn)
                    elif anomaly_detail == 'scratch':
                        detail_answer = 'There {} {} scratch{} at the {} of the {}.'.format(be, num, es, p,
                                                                                            get_class_name(class_name))
                        detail_answer_cn = '{}的图像中有{}道划痕，在图像的{}。'.format(get_class_name_cn(class_name), num,
                                                                                    p_cn)
                    else:
                        flag = 0
                        conversation = conversation[:-1]

                if flag:
                    if not Use_chinese:
                        conversation.append({"from": "gpt", "value": detail_answer})
                    else:
                        conversation.append({"from": "gpt", "value": detail_answer_cn})


                if class_name == 'SEM-image':
                    if anomaly_detail == "particle" or anomaly_detail == "hole" or anomaly_detail == "scratch" or anomaly_detail == "pattern_deform":
                        if not Use_chinese:
                            if anomaly_detail == 'hole':
                                conversation.append({"from": "human", "value": random.choice(class_questions_cause)})
                                conversation.append({"from": "gpt", "value": random.choice(single_answers_cause['hole'])})
                                conversation.append({"from": "human", "value": random.choice(class_questions_cause_solve)})
                                conversation.append({"from": "gpt", "value": random.choice(single_answers_cause_solve['hole'])})
                            elif anomaly_detail == 'scratch':
                                conversation.append({"from": "human", "value": random.choice(class_questions_cause)})
                                conversation.append({"from": "gpt", "value": random.choice(single_answers_cause['scratch'])})
                                conversation.append({"from": "human", "value": random.choice(class_questions_cause_solve)})
                                conversation.append({"from": "gpt", "value": random.choice(single_answers_cause_solve['scratch'])})
                            elif anomaly_detail == 'particle':
                                conversation.append({"from": "human", "value": random.choice(class_questions_cause)})
                                conversation.append({"from": "gpt", "value": random.choice(single_answers_cause['particle'])})
                                conversation.append({"from": "human", "value": random.choice(class_questions_cause_solve)})
                                conversation.append({"from": "gpt", "value": random.choice(single_answers_cause_solve['particle'])})
                            elif anomaly_detail == 'pattern_deform':
                                conversation.append({"from": "human", "value": random.choice(class_questions_cause)})
                                conversation.append({"from": "gpt", "value": random.choice(single_answers_cause['pattern_deform'])})
                                conversation.append({"from": "human", "value": random.choice(class_questions_cause_solve)})
                                conversation.append({"from": "gpt", "value": random.choice(single_answers_cause_solve['pattern_deform'])})
                        else:
                            if anomaly_detail == 'hole':
                                conversation.append({"from": "human", "value": random.choice(class_questions_cause_cn)})
                                conversation.append({"from": "gpt", "value": random.choice(single_answers_cause_cn['hole'])})
                                conversation.append(
                                    {"from": "human", "value": random.choice(class_questions_cause_solve_cn)})
                                conversation.append({"from": "gpt", "value": random.choice(single_answers_cause_solve_cn['hole'])})
                            elif anomaly_detail == 'scratch':
                                conversation.append({"from": "human", "value": random.choice(class_questions_cause_cn)})
                                conversation.append({"from": "gpt", "value": random.choice(single_answers_cause_cn['scratch'])})
                                conversation.append(
                                    {"from": "human", "value": random.choice(class_questions_cause_solve_cn)})
                                conversation.append({"from": "gpt", "value": random.choice(single_answers_cause_solve_cn['scratch'])})
                            elif anomaly_detail == 'particle':
                                conversation.append({"from": "human", "value": random.choice(class_questions_cause_cn)})
                                conversation.append({"from": "gpt", "value": random.choice(single_answers_cause_cn['particle'])})
                                conversation.append(
                                    {"from": "human", "value": random.choice(class_questions_cause_solve_cn)})
                                conversation.append({"from": "gpt", "value": random.choice(single_answers_cause_solve_cn['particle'])})
                            elif anomaly_detail == 'pattern_deform':
                                conversation.append({"from": "human", "value": random.choice(class_questions_cause_cn)})
                                conversation.append({"from": "gpt", "value": random.choice(single_answers_cause_cn['pattern_deform'])})
                                conversation.append(
                                    {"from": "human", "value": random.choice(class_questions_cause_solve_cn)})
                                conversation.append(
                                    {"from": "gpt", "value": random.choice(single_answers_cause_solve_cn['pattern_deform'])})




            if not Use_chinese:
                if r == 1 and 'mvtec_loco_anomaly_detection' not in img_path:
                    conversation.append({"from": "human", "value": random.choice(class_questions)})
                    if class_name not in MULTI_CLASS:
                        conversation.append(
                            {"from": "gpt", "value": random.choice(single_answers).format(get_class_name(class_name))})
                    else:
                        conversation.append(
                            {"from": "gpt", "value": random.choice(multi_answers).format(get_class_name(class_name))})
                # if r == 1 and 'mvtec_loco_anomaly_detection' not in img_path:
                    # conversation.append({"from": "human", "value": random.choice(class_questions_what).format('SEM')})
                    # conversation.append(
                    #         {"from": "gpt", "value": random.choice(single_answers_what['SEM'])})
            else:
                if r == 1 and 'mvtec_loco_anomaly_detection' not in img_path:
                    conversation.append({"from": "human", "value": random.choice(class_questions_cn)})
                    if class_name not in MULTI_CLASS:
                        conversation.append({"from": "gpt", "value": random.choice(single_answers_cn).format(
                            get_class_name_cn(class_name))})
                    else:
                        conversation.append(
                            {"from": "gpt", "value": random.choice(multi_answers_cn).format(get_class_name_cn(class_name))})
                # if r == 1 and 'mvtec_loco_anomaly_detection' not in img_path:
                    # conversation.append({"from": "human", "value": random.choice(class_questions__what_cn).format('SEM')})
                    # conversation.append(
                    #         {"from": "gpt", "value": random.choice(single_answers_what_cn['SEM'])})

            pairs = [conversation[i:i+2] for i in range(0, len(conversation), 2)]
            random.shuffle(pairs)
            conversation = [item for pair in pairs for item in pair]

        else:
            random_numbers = random.sample(range(1, 101), 6)
            if not Use_chinese:
                for i in random_numbers:
                    conversation.append({"from": "human", "value": questions_dict[i]})
                    conversation.append(
                            {"from": "gpt", "value": answers_dict[i]})
            else:
                for i in random_numbers:
                    conversation.append({"from": "human", "value": questions_dict_chinese[i]})
                    conversation.append(
                        {"from": "gpt", "value": answers_dict_chinese[i]})

        # m = random.randint(0, 1)
        # if m == 0:
        #     return img, conversation1, class_name, mask, img_path
        # else:
        #     return img, conversation, class_name, mask, img_path

        # conversation2 = [conversation, conversation1, conversation1]
        return img, conversation, class_name, mask, img_path



    def collate(self, instances):
        images = []
        texts = []
        class_names = []
        masks = []
        img_paths = []
        for instance in instances:
            images.append(instance[0])
            texts.append(instance[1])
            class_names.append(instance[2])
            masks.append(instance[3])
            if 'SEM' in instance[4] or 'visa' in instance[4] or 'mvtec_loco_anomaly_detection' in instance[4]:
                img_paths.append(instance[4])

        return dict(
            images=images,
            texts=texts,
            class_names=class_names,
            masks=masks,
            img_paths=img_paths,
        )
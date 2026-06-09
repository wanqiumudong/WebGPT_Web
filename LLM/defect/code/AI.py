import time  # 导入time模块

from flask import Flask, request, jsonify
from flask_cors import CORS
from transformers import AutoModelForCausalLM, AutoTokenizer
import torch
import json

app = Flask(__name__)
CORS(app)

model_name = "Qwen/Qwen2.5-7B-Instruct"
model = AutoModelForCausalLM.from_pretrained(
    model_name,
    torch_dtype="auto",
    device_map="auto"
)
tokenizer = AutoTokenizer.from_pretrained(model_name)

# 用于存储每个用户的对话历史
conversation_history = {}

# 五个指定问答对
predefined_qas = {
    "请介绍一下自己的功能": """
我是由于浙江大学团队进行开发的医学领域大模型，我的团队成员包括：姜钰琪、钟欢欣、钟卓霖和余涛。我具备多种功能，能够在临床、研究和公共卫生等多个领域提供支持。以下是我支持的一些主要功能：：

1. 疾病预测与诊断：
通过对医学影像（如CT、MRI、X光等）、基因组数据和临床记录的分析，医学大模型可以帮助医生进行疾病预测、早期诊断和个性化治疗方案的推荐。例如，癌症、心脏病、神经退行性疾病等的早期检测。
2. 医学影像分析：
自动化医学影像分析技术可以识别和分类疾病的迹象，如肿瘤、病变、出血等，减少医生在图像解读时的工作负担，并提高诊断的准确性。
3. 个性化医疗：
利用患者的历史医疗数据、基因组信息和生活方式数据，模型可以预测个体对某些药物的反应，帮助医生制定个性化的治疗方案。
4. 药物研发与临床试验：
在药物研发中，医学大模型可以用于筛选潜在药物候选分子、预测药物效果和毒性、设计临床试验方案等。它们也能分析和解读临床试验数据，以更快地推进新药的开发。
5. 公共卫生监测与预测：
基于大规模的公共卫生数据，模型可以预测传染病的流行趋势、评估公共卫生政策的效果，并帮助制定应急响应措施。
6. 智能辅助决策：
提供基于数据的临床决策支持，帮助医生在治疗过程中做出更精准的判断。例如，通过结合患者的症状、体征、实验室检查结果等信息，智能系统可以推荐治疗方案或预警潜在风险。
7. 健康管理与预防：
基于个人健康档案和生活习惯，模型可以进行健康风险评估，提供健康指导和早期预警，促进疾病预防等。

请输入您需要查询的相关问题！
""",
    "急性ST段抬高型心肌梗死（STEMI）患者在急诊接诊时，为什么必须立即进行一般处理？具体措施包括哪些内容，各自有何生理学意义？": """
急性STEMI患者由于心肌急性缺血坏死，病情危重，迅速稳定生命体征是抢救的前提。一般处理包括以下几方面：

1. 卧床休息：减少体力活动，降低交感神经兴奋，减轻心脏负荷，从而减少心肌耗氧量；
2. 吸氧：通过鼻导管或面罩供氧，维持血氧饱和度在94%–98%，以改善心肌缺氧环境，尤其适用于低氧血症患者；
3. 心电监护：实时监测心率、心律及血压变化，有助于及早发现并发的严重心律失常、心力衰竭等，便于及时处理。

这些措施为再灌注治疗及药物干预赢得时间，并可降低并发症风险，提高生存率。
""",
    "STEMI患者的再灌注治疗为何至关重要？请比较直接经皮冠状动脉介入治疗（PCI）与溶栓治疗的适应证、操作方式及利弊。": """
再灌注治疗的目标是尽早开通闭塞的冠状动脉，恢复心肌血流，挽救濒死心肌，减少梗死面积，改善远期预后。

1. 直接PCI是首选方案，适用于发病12小时内、特别是3–6小时内的患者，或虽超过12小时但仍有胸痛、血流动力学不稳定者。通过导管将球囊或支架送入堵塞血管段，快速恢复血流。优点是血管开通率高、再梗死率低，但需具备相应条件和经验的医疗团队。
2. 溶栓治疗适用于不能在120分钟内完成PCI的患者，要求无禁忌证。药物如尿激酶、rt-PA等可静脉溶解血栓。其优点是实施方便，尤其适用于基层医疗机构；但存在出血风险、血管再闭率较高，且不适合某些高龄或有出血史的患者。

两者均需在溶栓或PCI后继续抗栓治疗，并警惕再灌注引起的并发症。
""",
    "在STEMI的药物治疗中，抗血小板、抗凝、β受体阻滞剂及硝酸酯类药物各有何作用机制和使用原则？如何合理联合应用以优化治疗效果？": """
药物治疗是再灌注手段的有力补充，关键在于控制血栓形成、减少心肌耗氧、缓解症状。

1. 抗血小板药物：如阿司匹林与P2Y₁₂受体拮抗剂（如氯吡格雷或替格瑞洛）联合使用，能有效抑制血小板活化与聚集，预防动脉血栓形成，是STEMI治疗的基石。应尽早使用负荷剂量，随后维持治疗。
2. 抗凝药物：如普通肝素或低分子肝素（依诺肝素），通过增强抗凝血酶Ⅲ活性抑制凝血因子，预防血栓进一步扩展。对有肝素过敏或肝素诱导的血小板减少症者，可使用比伐芦定。
3. β受体阻滞剂：如美托洛尔，通过减慢心率、降低心肌收缩力来减少心肌耗氧，适用于无严重心衰、低血压等禁忌的患者。
4. 硝酸酯类药物：如硝酸甘油，可扩张静脉和冠状动脉，降低前后负荷、缓解胸痛。但应避免在血压偏低患者中使用。

联合用药时需密切监测患者生命体征，权衡出血风险与心肌保护效益，个体化制定方案。


""",
    "我需要定义一个用于约束测量的查找表。这个查找表的第一个维度范围是[0.04,5.52]，第二个维度范围也是[0.04,5.52]。尺寸是5x5，中间的点使用线性插值的方法来拟合。在使用时这个查找表的编号为2": """
为了定义一个用于约束测量的查找表，我们可以按照以下步骤进行：定义查找表的索引、使用线性插值方法填充查找表和确保查找表的编号为2。
define_template -type constraint \\
         -index_1 {0.04 1.41 2.78 4.15 5.52} \\
         -index_2 {0.04 1.41 2.78 4.15 5.52} \\
         constraint_template_2
""",
    "请你为我撰写详细的测量策略脚本。我的测量环境为：电压1.5V，温度125摄氏度。我需要保存所有的spice文件到 ./spice_deck 路径下。我的仿真器需要设置为gear方法，gmin参数为1e-15。仿真时候所用的spice模型文件在 ./spice_model文件夹下，测量hold约束的时候采用glitch方法。你测量时所使用的模板在template.tcl中": """
为了实现你的测量策略需求，我们可以编写一个详细的测量策略脚本。这个脚本将包括以下几个部分：设置仿真环境和参数、指定仿真器参数、设置SPICE模型文件路径、保存SPICE文件到指定路径和进行仿真并处理结果。
# 设置工作目录
set localdir $env(PWD)
set rundir ./rundir
# 设置目录路径
set_var extsim_deck_dir "./spice_deck"
set_var template_dir "template.tcl"
set_var spice_model_dir "./spice_model"
# 设置电压和温度
set_operating_condition -voltage 1.5 -temp 125
# 保存 spice deck 文件设置
set_var extsim_save_passed all
set_var extsim_save_failed all
# 仿真器配置
set_var extsim_option "method=gear gmin=1e-15"
# 读取 SPICE 文件
set spicefiles ./spice_model/spice_modle.sp
lappend spicefiles DQV1_9TV50.sp
read_spice -format spectre $spicefiles
# 创建必要的目录
exec mkdir -p ${rundir}/LIBRARY
# 设定测量hold约束的时候采用glitch方法
set_var constraint_glitch_hold 1
# 开始仿真
char_library -cells DQV1_9TV50
# 写入库文件
write_library ${rundir}/LIBRARY/nldm.lib
"""
}

@app.route('/generate', methods=['POST'])
def generate_response():
    data = json.loads(request.data)
    user_message = data.get('message', '')
    user_id = data.get('user_id', 'default')  # 使用用户ID来区分不同的会话

    # 如果该用户没有历史记录，初始化一个空的历史
    if user_id not in conversation_history:
        conversation_history[user_id] = []

    # 更新会话历史
    conversation_history[user_id].append({"role": "user", "content": user_message})

    # 判断用户输入是否属于指定问题
    if user_message in predefined_qas:
        # 在回答指定问题时延迟一段时间
        time.sleep(5) 

        response = predefined_qas[user_message]
    else:
        # 使用系统消息和用户历史生成输入
        messages = [{"role": "system", "content": "You are FabGPT, created by zju. You are a helpful assistant."}]
        messages.extend(conversation_history[user_id])  # 将历史消息添加到当前对话中

        # 生成文本
        text = tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True
        )
        model_inputs = tokenizer([text], return_tensors="pt").to(model.device)

        # 生成响应
        generated_ids = model.generate(
            **model_inputs,
            max_new_tokens=512
        )
        generated_ids = [
            output_ids[len(input_ids):] for input_ids, output_ids in zip(model_inputs.input_ids, generated_ids)
        ]

        response = tokenizer.batch_decode(generated_ids, skip_special_tokens=True)[0]

    # 将生成的响应添加到历史记录中
    conversation_history[user_id].append({"role": "assistant", "content": response})

    return jsonify({"role": "assistant", "content": response})

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5002)

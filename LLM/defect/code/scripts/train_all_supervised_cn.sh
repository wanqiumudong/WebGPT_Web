#!/bin/bash

deepspeed --include localhost:5 --master_port 28410 train_all_supervised_cn.py \
    --model openllama_peft \
    --stage 1\
    --imagebind_ckpt_path /data/Web-FabGPT/LLM/defect/pretrained_ckpt/imagebind_ckpt/imagebind_huge.pth\
    --vicuna_ckpt_path /data/Web-FabGPT/LLM/defect/pretrained_ckpt/vicuna_ckpt/7b_v0/\
    --delta_ckpt_path /data/Web-FabGPT/LLM/defect/pretrained_ckpt/pandagpt_ckpt/7b/pytorch_model.pt\
    --max_tgt_len 1024\
    --data_path  /home/yqjiang/data/project/FabThink-qwen2vl-7b/data/pandagpt4_visual_instruction_data.json\
    --image_root_path /home/yqjiang/data/project/FabThink-qwen2vl-7b/data/images/\
    --save_path  /data/Web-FabGPT/LLM/defect/code/ckpt-daluan/sim-50/\
    --log_path /data/Web-FabGPT/LLM/defect/code/ckpt-daluan/sim-50/log_rest/;
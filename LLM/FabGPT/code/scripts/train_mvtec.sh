#!/bin/bash

deepspeed --include localhost:3 --master_port 28420 train_mvtec.py \
    --model openllama_peft \
    --stage 1\
    --imagebind_ckpt_path /data/Web-FabGPT/LLM/FabGPT/pretrained_ckpt/imagebind_ckpt/imagebind_huge.pth\
    --vicuna_ckpt_path /data/Web-FabGPT/LLM/FabGPT/pretrained_ckpt/vicuna_ckpt/7b_v0/\
    --delta_ckpt_path /data/Web-FabGPT/LLM/FabGPT/pretrained_ckpt/pandagpt_ckpt/7b/pytorch_model.pt\
    --max_tgt_len 1024\
    --data_path  /home/yqjiang/data/project/FabThink-qwen2vl-7b/data/pandagpt4_visual_instruction_data.json\
    --image_root_path /home/yqjiang/data/project/FabThink-qwen2vl-7b/data/images/\
    --save_path  /data/Web-FabGPT/LLM/FabGPT/code/ckpt-TCAD/unsupervised-sem/\
    --log_path /data/Web-FabGPT/LLM/FabGPT/code/ckpt-TCAD/unsupervised-sem/log_rest/;

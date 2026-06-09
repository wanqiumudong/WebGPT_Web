import request from '../utils/request';
import { fetchType } from './constant';
import axios from "axios";
import { CHATBOT_BASE_URL } from '../config/endpoints';
import { createStreamRequest } from '../utils/apiUtils';

const CHATBOT_API_BASE_URL = CHATBOT_BASE_URL;

export const fetchChatBot = async (data) => {
  try {
    const response = await request({
      baseUrl: CHATBOT_API_BASE_URL,
      url: '/generate',
      data,
      method: fetchType.post
    });
    return response.content || response.response;
  } catch (e) {
    console.error('Chatbot请求失败:', e);
    return '后端算法还在优化中哦';
  }
};

export const fetchChatBotStreaming = (data, onChunkReceived, onComplete, onError) => {
  const streamRequest = createStreamRequest({
    baseUrl: CHATBOT_API_BASE_URL,
    data,
    onChunk: (streamData) => {
      onChunkReceived && onChunkReceived(streamData);
    },
    onComplete,
    onError: (error) => {
      console.error('Chatbot流式API请求出错:', error);
      onError && onError(error);
    }
  });

  return {
    cancel: async () => {
      try {
        if (onChunkReceived) {
          onChunkReceived({
            aborted: true,
            chunk: "",
            is_complete: true
          });
        }

        await streamRequest.cancel();
        onComplete && onComplete();
        return true;
      } catch (e) {
        console.error('取消Chatbot流式请求时出错:', e);
        onComplete && onComplete();
        throw e;
      }
    },
    getRequestId: () => streamRequest.getRequestId()
  };
};

export const uploadFile = (formData, config) => {
  return axios
    .post(`${CHATBOT_API_BASE_URL}/uploadFile`, formData, { ...config })
    .catch((e) => {
      console.error('❌ Chatbot文件上传失败:', e);
      return e;
    });
};

export const deleteUploadedFile = (data) => {
  return axios
    .post(`${CHATBOT_API_BASE_URL}/deleteFile`, data, {
      headers: {
        'Content-Type': 'application/json'
      }
    })
    .catch((e) => {
      console.error('❌ Chatbot文件删除失败:', e);
      return e;
    });
};

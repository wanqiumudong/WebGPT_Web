import request from '../utils/request';
import { fetchType } from './constant';
import axios from "axios";

const CHATBOT_LOAD_BALANCER_URL = 'http://10.98.64.22:5104';

const parseSSEData = (line, serviceName) => {
  if (!line.startsWith('data: ')) {
    return null;
  }

  try {
    return JSON.parse(line.substring(6));
  } catch (e) {
    console.warn(`${serviceName} SSE数据解析失败:`, e);
    return null;
  }
};

export const fetchChatBot = async (data) => {
  try {
    const response = await request({
      baseUrl: CHATBOT_LOAD_BALANCER_URL,
      url: '/generate',
      data,
      method: fetchType.post
    });
    return response.content || response.response;
  } catch (e) {
    console.error('Chatbot负载均衡器请求失败:', e);
    return '后端算法还在优化中哦';
  }
};

export const fetchChatBotStreaming = (data, onChunkReceived, onComplete, onError) => {
  const url = `${CHATBOT_LOAD_BALANCER_URL}/stream_generate`;
  const controller = new AbortController();
  const { signal } = controller;
  let requestId = null;

  const fetchStream = async () => {
    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(data),
        signal
      });

      if (!response.ok) {
        throw new Error(`HTTP error! Status: ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();

      const processStream = async ({ done, value }) => {
        if (done) {
          onComplete && onComplete();
          return;
        }

        const chunk = decoder.decode(value);
        const lines = chunk.split('\n\n');

        for (const line of lines) {
          const streamData = parseSSEData(line, 'Chatbot');
          if (!streamData) {
            continue;
          }

          if (streamData.start_streaming && streamData.request_id) {
            requestId = streamData.request_id;
          }

          onChunkReceived && onChunkReceived(streamData);

          if (streamData.is_complete || streamData.aborted) {
            onComplete && onComplete();
            return;
          }
        }

        try {
          const result = await reader.read();
          return processStream(result);
        } catch (e) {
          if (e.name !== 'AbortError') {
            throw e;
          }
        }
      };

      reader.read().then(processStream);
    } catch (e) {
      if (e.name !== 'AbortError') {
        console.error('Chatbot流式API请求出错:', e);
        onError && onError(e);
      }
    }
  };

  fetchStream();

  return {
    cancel: async () => {
      try {
        if (requestId) {
          try {
            await fetch(`${CHATBOT_LOAD_BALANCER_URL}/abort_stream`, {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json'
              },
              body: JSON.stringify({ request_id: requestId })
            });
          } catch (e) {
            console.error('发送中止请求到Chatbot负载均衡器时出错:', e);
          }
        }

        if (onChunkReceived) {
          onChunkReceived({
            aborted: true,
            chunk: "",
            is_complete: true
          });
        }

        controller.abort();
        onComplete && onComplete();
        return true;
      } catch (e) {
        console.error('取消Chatbot流式请求时出错:', e);
        onComplete && onComplete();
        throw e;
      }
    },
    getRequestId: () => requestId
  };
};

export const uploadFile = (formData, config) => {
  return axios
    .post(`${CHATBOT_LOAD_BALANCER_URL}/uploadFile`, formData, { ...config })
    .catch((e) => {
      console.error('❌ Chatbot文件上传失败:', e);
      return e;
    });
};

export const deleteUploadedFile = (data) => {
  return axios
    .post(`${CHATBOT_LOAD_BALANCER_URL}/deleteFile`, data, {
      headers: {
        'Content-Type': 'application/json'
      }
    })
    .catch((e) => {
      console.error('❌ Chatbot文件删除失败:', e);
      return e;
    });
};

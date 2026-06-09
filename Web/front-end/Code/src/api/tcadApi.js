import request from '../utils/request';
import { fetchType } from './constant';
import axios from "axios";
import { TCAD_BASE_URL } from '../config/endpoints';
import { createStreamRequest } from '../utils/apiUtils';

const TCAD_LOAD_BALANCER_URL = TCAD_BASE_URL;

export const fetchTCAD = async (data) => {
  try {
    const response = await request({
      baseUrl: TCAD_LOAD_BALANCER_URL,
      url: '/generate',
      data,
      method: fetchType.post
    });
    return response.content || response.response;
  } catch (e) {
    console.error('TCAD负载均衡器请求失败:', e);
    return '后端算法还在优化中哦';
  }
};

export const fetchTCADStreaming = (data, onChunkReceived, onComplete, onError) => {
  const streamRequest = createStreamRequest({
    baseUrl: TCAD_LOAD_BALANCER_URL,
    data,
    onChunk: (streamData) => {
      onChunkReceived && onChunkReceived(streamData);
    },
    onComplete,
    onError: (error) => {
      console.error('TCAD流式API请求出错:', error);
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
            is_complete: false
          });
        }

        await streamRequest.cancel();
        return true;
      } catch (e) {
        console.error('取消TCAD流式请求时出错:', e);
        throw e;
      }
    },
    getRequestId: () => streamRequest.getRequestId()
  };
};

export const uploadTCADFile = (formData, config) => {
  return axios
    .post(`${TCAD_LOAD_BALANCER_URL}/uploadFile`, formData, { ...config })
    .catch((e) => {
      console.error('❌ TCAD文件上传失败:', e);
      return e;
    });
};

export const deleteTCADUploadedFile = (data) => {
  return axios
    .post(`${TCAD_LOAD_BALANCER_URL}/deleteFile`, data, {
      headers: {
        'Content-Type': 'application/json'
      }
    })
    .catch((e) => {
      console.error('❌ TCAD文件删除失败:', e);
      return e;
    });
};

const fetchTCADJson = async (path, params = {}) => {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && `${value}`.trim() !== '') {
      query.set(key, value);
    }
  });
  const queryText = query.toString();
  const url = `${TCAD_LOAD_BALANCER_URL}${path}${queryText ? `?${queryText}` : ''}`;
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`HTTP error! Status: ${response.status}`);
  }
  return response.json();
};

export const buildTCADSessionExportUrl = ({ user_id, conversation_id, format = 'markdown' }) => {
  const query = new URLSearchParams();
  if (user_id) {
    query.set('user_id', user_id);
  }
  if (conversation_id) {
    query.set('conversation_id', conversation_id);
  }
  if (format) {
    query.set('format', format);
  }
  const queryText = query.toString();
  return `${TCAD_LOAD_BALANCER_URL}/session_export${queryText ? `?${queryText}` : ''}`;
};

export const fetchTCADDemoCases = async (limit = 8) => fetchTCADJson('/demo_cases', { limit });

export const fetchTCADSessionSummary = async ({ user_id, conversation_id }) =>
  fetchTCADJson('/session_summary', { user_id, conversation_id });

export const fetchTCADReferencePreview = async ({ user_id, conversation_id, ref_id }) =>
  fetchTCADJson('/reference_preview', { user_id, conversation_id, ref_id });

export const fetchTCADArtifactPreview = async ({ user_id, conversation_id, artifact_key, max_lines = 80 }) =>
  fetchTCADJson('/artifact_preview', { user_id, conversation_id, artifact_key, max_lines });

export const fetchTCADBriefSummary = async ({ user_id, conversation_id }) =>
  fetchTCADJson('/brief_summary', { user_id, conversation_id });

export const fetchTCADValidationSummary = async ({ user_id, conversation_id }) =>
  fetchTCADJson('/validation_summary', { user_id, conversation_id });

export const fetchTCADWorkspaceManifest = async ({ user_id, conversation_id }) =>
  fetchTCADJson('/workspace_manifest', { user_id, conversation_id });

export const fetchTCADWorkspacePreview = async ({ user_id, conversation_id, path, max_lines = 80 }) =>
  fetchTCADJson('/workspace_preview', { user_id, conversation_id, path, max_lines });

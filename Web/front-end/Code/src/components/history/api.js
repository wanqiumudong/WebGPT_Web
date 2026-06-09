import { fetchType } from '../../api/constant';
import request from '../../utils/request';
import { BACKEND_BASE_URL } from '../../config/endpoints';

export const addSession = async (data) => {
    try {
        const response = await request({
            baseUrl: BACKEND_BASE_URL,
            url: '/session/add',
            data,
            method: fetchType.post,
            headers: {
                'Content-Type': 'application/json'
            }
        });

        // request() 可能返回数字状态码，或JSON对象
        if (response === 200 || response === 201) {
            return { success: true };
        }

        if (response && typeof response === 'object') {
            if (response.success === true || response.sessionId || response.session_id) {
                return response;
            }
        }

        console.error('addSession failed:', response);
        return null;
    } catch (e) {
        console.error(e);  // 输出错误以便调试
        return null;
    }
};

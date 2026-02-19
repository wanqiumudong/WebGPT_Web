import { fetchType } from '../../api/constant';
import request from '../../utils/request';


export const addSession = async (data, port) => {
    try {
        const response = await request({
            baseUrl: 'http://10.98.64.22:8080',
            url: '/session/add',
            data,
            method: fetchType.post,
            headers: {
                'Content-Type': 'application/json'
            }
        });

        // 检查后端返回的状态码，如果是 201 则正常返回内容
        if (response === 201) {
            return response;
        } else {
            // 其他非 201 的状态码，可以根据需要自定义处理
            return `请求失败，状态码: ${response.status}`;
        }
    } catch (e) {
        // 错误捕获
        console.error(e);  // 输出错误以便调试
        return '后端算法还在优化中哦';
    }
}


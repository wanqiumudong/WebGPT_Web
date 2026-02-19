#!/bin/bash
# user_manager.sh - 简洁版用户管理工具

API_BASE="http://localhost:5203/api"
ORIGINAL_API="http://10.98.64.22:8080"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# 验证密码格式（仅用于创建用户）
validate_password() {
    local password="$1"
    
    # 检查长度
    if [ ${#password} -lt 8 ] || [ ${#password} -gt 16 ]; then
        echo "密码长度必须为8-16位"
        return 1
    fi
    
    # 检查是否包含小写字母
    if ! [[ "$password" =~ [a-z] ]]; then
        echo "密码必须包含小写字母"
        return 1
    fi
    
    # 检查是否包含大写字母
    if ! [[ "$password" =~ [A-Z] ]]; then
        echo "密码必须包含大写字母"
        return 1
    fi
    
    # 检查是否包含数字
    if ! [[ "$password" =~ [0-9] ]]; then
        echo "密码必须包含数字"
        return 1
    fi
    
    # 检查是否包含符号
    if ! [[ "$password" =~ [^a-zA-Z0-9] ]]; then
        echo "密码必须包含符号"
        return 1
    fi
    
    return 0
}

# 显示用户列表
show_users() {
    echo -e "${BLUE}===== FabGPT 用户管理系统 =====${NC}"
    echo ""
    
    users_json=$(curl -s $API_BASE/users)
    curl_exit_code=$?
    
    if [ $curl_exit_code -ne 0 ] || [ -z "$users_json" ]; then
        echo -e "${RED}❌ 无法连接到用户管理服务${NC}"
        return 1
    fi
    
    echo "$users_json" | python3 -c "
import json, sys

try:
    users = json.loads(sys.stdin.read())
    
    print('{:<3} {:<12} {:<20} {:<12} {:<8} {:<8} {:<25} {:<20}'.format(
        'ID', '用户名', '邮箱', '单位', '职位', '状态', '有效期', '最后登录'))
    print('=' * 110)
    
    for user in users:
        user_id = str(user.get('userId', ''))
        username = user.get('username', '')[:11]
        email = user.get('email', '')[:19]
        affiliation = (user.get('affiliation') or '未设置')[:11]
        position = (user.get('position') or '未设置')[:7]
        
        status_text = '启用' if user.get('status') == 1 else '禁用'
        
        expire_date = user.get('expireDate')
        if expire_date:
            expire_text = expire_date[:19]
            if user.get('expired', False):
                expire_text += ' (已过期)'
        else:
            expire_text = '永久有效'
        
        last_login = user.get('lastLogin', '')[:19] if user.get('lastLogin') else '从未登录'
        
        print('{:<3} {:<12} {:<20} {:<12} {:<8} {:<8} {:<25} {:<20}'.format(
            user_id, username, email, affiliation, position, status_text, expire_text, last_login))
        
except Exception as e:
    print('解析数据出错:', str(e))
"
    
    echo ""
    echo -e "${YELLOW}===== 使用说明 =====${NC}"
    echo -e "${GREEN}用户管理操作：${NC}"
    echo "  ./user_manager.sh create                       - 创建新用户"
    echo "  ./user_manager.sh disable <用户ID>             - 禁用指定用户"
    echo "  ./user_manager.sh enable <用户ID>              - 启用指定用户"
    echo "  ./user_manager.sh delete <用户ID>              - 删除指定用户"
    echo ""
    echo -e "${GREEN}密码管理操作：${NC}"
    echo "  ./user_manager.sh reset-password <用户ID>      - 重置用户密码"
    echo ""
    echo -e "${GREEN}有效期管理：${NC}"
    echo "  ./user_manager.sh set-expire <用户ID> <日期>   - 设置用户有效期"
    echo "  ./user_manager.sh set-expire <用户ID>          - 设置为永久有效"
    echo "    日期格式: 'YYYY-MM-DD HH:MM:SS'"
    echo "    示例: '2025-12-31 23:59:59'"
    echo ""
    echo -e "${GREEN}使用示例：${NC}"
    echo "  ./user_manager.sh create                       # 交互式创建用户"
    echo "  ./user_manager.sh reset-password 7             # 重置ID为7的用户密码"
    echo "  ./user_manager.sh set-expire 4 '2025-12-31 23:59:59'  # 设置有效期"
    echo "  ./user_manager.sh disable 3                    # 禁用ID为3的用户"
    echo "  ./user_manager.sh enable 3                     # 启用ID为3的用户"
}

# 创建用户
create_user() {
    echo -e "${BLUE}===== 创建新用户 =====${NC}"
    
    read -p "用户名: " username
    read -p "邮箱: " email
    read -p "单位: " affiliation
    read -p "职位: " position
    
    echo ""
    echo -e "${YELLOW}密码要求：${NC}"
    echo -e "  • 长度：8-16位"
    echo -e "  • 必须包含：大写字母、小写字母、数字、符号"
    echo -e "  • 示例：${GREEN}Abc123!@${NC}"
    echo ""
    
    while true; do
        read -sp "密码: " password
        echo ""
        read -sp "确认密码: " password_confirm
        echo ""
        
        if [ "$password" != "$password_confirm" ]; then
            echo -e "${RED}❌ 两次输入的密码不匹配，请重新输入${NC}"
            continue
        fi
        
        if [ -z "$password" ]; then
            echo -e "${RED}❌ 密码不能为空，请重新输入${NC}"
            continue
        fi
        
        # 验证密码格式
        validation_result=$(validate_password "$password")
        if [ $? -eq 0 ]; then
            break
        else
            echo -e "${RED}❌ $validation_result，请重新输入${NC}"
        fi
    done
    
    if [ -z "$username" ] || [ -z "$email" ]; then
        echo -e "${RED}❌ 用户名和邮箱不能为空${NC}"
        return 1
    fi
    
    json_data="{\"username\":\"$username\",\"email\":\"$email\",\"password\":\"$password\""
    if [ ! -z "$affiliation" ]; then
        json_data="$json_data,\"affiliation\":\"$affiliation\""
    fi
    if [ ! -z "$position" ]; then
        json_data="$json_data,\"position\":\"$position\""
    fi
    json_data="$json_data}"
    
    echo -e "${YELLOW}正在创建用户...${NC}"
    
    response=$(curl -s -X POST $ORIGINAL_API/user/add \
        -H "Content-Type: application/json" \
        -d "$json_data")
    
    # 检查响应是否包含错误
    if echo "$response" | grep -q '"status":500'; then
        error_msg=$(echo "$response" | python3 -c "
import json, sys
try:
    data = json.loads(sys.stdin.read())
    print(data.get('details', '未知错误'))
except:
    print('响应解析失败')
")
        echo -e "${RED}❌ 创建失败：$error_msg${NC}"
        return 1
    fi
    
    sleep 2
    echo -e "${GREEN}✅ 用户创建成功！${NC}"
    show_users
}

# 设置有效期
set_expire() {
    local user_id=$1
    local expire_date=$2
    
    if [ -z "$user_id" ]; then
        echo -e "${RED}❌ 用法: $0 set-expire <用户ID> [日期|permanent]${NC}"
        echo -e "${YELLOW}示例:${NC}"
        echo "  $0 set-expire 4 '2025-12-31 23:59:59'  # 设置具体日期"
        echo "  $0 set-expire 4 permanent               # 设置为永久有效"
        echo "  $0 set-expire 4                         # 设置为永久有效"
        return 1
    fi
    
    # 处理永久有效的情况
    if [ -z "$expire_date" ] || [ "$expire_date" = "permanent" ] || [ "$expire_date" = "forever" ]; then
        json_data='{"expireDate":null}'
        echo -e "${YELLOW}设置为永久有效${NC}"
    else
        # 验证日期格式（简单验证）
        if [[ ! "$expire_date" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}.*$ ]]; then
            echo -e "${RED}❌ 日期格式错误，请使用 YYYY-MM-DD HH:MM:SS 格式${NC}"
            return 1
        fi
        
        json_data="{\"expireDate\":\"$expire_date\"}"
        echo -e "${YELLOW}设置有效期至: $expire_date${NC}"
    fi
    
    response=$(curl -s -X PUT $API_BASE/users/$user_id/expire \
        -H "Content-Type: application/json" \
        -d "$json_data")
    
    echo -e "${GREEN}✅ 有效期设置完成${NC}"
    show_users
}

# 禁用用户
disable_user() {
    local user_id=$1
    
    if [ -z "$user_id" ]; then
        echo -e "${RED}❌ 用法: $0 disable <用户ID>${NC}"
        return 1
    fi
    
    curl -s -X PUT $API_BASE/users/$user_id/disable > /dev/null
    echo -e "${GREEN}✅ 用户已禁用${NC}"
    show_users
}

# 启用用户
enable_user() {
    local user_id=$1
    
    if [ -z "$user_id" ]; then
        echo -e "${RED}❌ 用法: $0 enable <用户ID>${NC}"
        return 1
    fi
    
    curl -s -X PUT $API_BASE/users/$user_id/enable > /dev/null
    echo -e "${GREEN}✅ 用户已启用${NC}"
    show_users
}

# 重置密码
reset_password() {
    local user_id=$1
    
    if [ -z "$user_id" ]; then
        echo -e "${RED}❌ 用法: $0 reset-password <用户ID>${NC}"
        echo -e "${YELLOW}说明: 重置指定用户的登录密码${NC}"
        echo -e "${YELLOW}示例: $0 reset-password 7${NC}"
        return 1
    fi
    
    # 获取用户信息
    user_info=$(curl -s $API_BASE/users/$user_id)
    
    if [ $curl_exit_code -ne 0 ] || [ -z "$user_info" ]; then
        echo -e "${RED}❌ 获取用户信息失败或用户不存在${NC}"
        return 1
    fi
    
    # 提取用户信息
    user_data=$(echo "$user_info" | python3 -c "
import json, sys
try:
    user = json.loads(sys.stdin.read())
    print(user.get('username', ''))
    print(user.get('email', ''))
    print(user.get('affiliation', ''))
    print(user.get('position', ''))
    print(user.get('status', 1))
except:
    print('')
    print('')
    print('')
    print('')
    print('1')
")
    
    # 读取用户数据到变量
    username=$(echo "$user_data" | sed -n '1p')
    email=$(echo "$user_data" | sed -n '2p')
    affiliation=$(echo "$user_data" | sed -n '3p')
    position=$(echo "$user_data" | sed -n '4p')
    status=$(echo "$user_data" | sed -n '5p')
    
    if [ -z "$username" ]; then
        echo -e "${RED}❌ 未找到ID为 $user_id 的用户${NC}"
        return 1
    fi
    
    echo -e "${BLUE}🔑 重置用户密码${NC}"
    echo -e "用户名: ${YELLOW}$username${NC}"
    echo -e "邮箱: ${YELLOW}$email${NC}"
    echo ""
    echo -e "${YELLOW}请设置新密码：${NC}"
    echo ""
    
    # 输入新密码（无格式检查）
    while true; do
        read -sp "请输入新密码: " new_password
        echo ""
        read -sp "请再次输入新密码: " password_confirm
        echo ""
        
        if [ "$new_password" != "$password_confirm" ]; then
            echo -e "${RED}❌ 两次输入的密码不匹配，请重新输入${NC}"
            continue
        fi
        
        if [ -z "$new_password" ]; then
            echo -e "${RED}❌ 密码不能为空，请重新输入${NC}"
            continue
        fi
        
        # 密码重置时不进行格式检查
        break
    done
    
    # 构建更新请求
    json_data="{\"username\":\"$username\",\"email\":\"$email\",\"password\":\"$new_password\""
    
    if [ ! -z "$affiliation" ] && [ "$affiliation" != "null" ]; then
        json_data="$json_data,\"affiliation\":\"$affiliation\""
    fi
    
    if [ ! -z "$position" ] && [ "$position" != "null" ]; then
        json_data="$json_data,\"position\":\"$position\""
    fi
    
    json_data="$json_data,\"status\":$status}"
    
    echo -e "${YELLOW}正在更新密码...${NC}"
    
    # 发送更新请求到原始API
    response=$(curl -s -X POST $ORIGINAL_API/user/update \
        -H "Content-Type: application/json" \
        -d "$json_data")
    
    # 检查响应是否包含错误
    if echo "$response" | grep -q '"status":500'; then
        error_msg=$(echo "$response" | python3 -c "
import json, sys
try:
    data = json.loads(sys.stdin.read())
    print(data.get('details', '未知错误'))
except:
    print('响应解析失败')
")
        echo -e "${RED}❌ 密码重置失败：$error_msg${NC}"
        return 1
    fi
    
    sleep 1
    echo -e "${GREEN}✅ 密码重置完成${NC}"
    echo -e "${YELLOW}⚠️ 重要提示: 用户需要使用新密码重新登录${NC}"
    show_users
}

# 删除用户
delete_user() {
    local user_id=$1
    
    if [ -z "$user_id" ]; then
        echo -e "${RED}❌ 用法: $0 delete <用户ID>${NC}"
        return 1
    fi
    
    # 获取用户信息确认
    user_info=$(curl -s $API_BASE/users/$user_id)
    username=$(echo "$user_info" | python3 -c "
import json, sys
try:
    user = json.loads(sys.stdin.read())
    print(user.get('username', ''))
except:
    print('')
")
    
    if [ -z "$username" ]; then
        echo -e "${RED}❌ 用户不存在${NC}"
        return 1
    fi
    
    echo -e "${RED}⚠️ 将删除用户: $username (ID: $user_id)${NC}"
    read -p "确认删除? (y/N): " confirm
    
    if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
        curl -s $ORIGINAL_API/user/delete?userId=$user_id > /dev/null
        sleep 2
        echo -e "${GREEN}✅ 用户删除请求已发送${NC}"
        show_users
    else
        echo -e "${YELLOW}操作已取消${NC}"
    fi
}

# 主程序
case "$1" in
    "create")
        create_user
        ;;
    "set-expire")
        set_expire $2 "$3"
        ;;
    "disable")
        disable_user $2
        ;;
    "enable")
        enable_user $2
        ;;
    "reset-password")
        reset_password $2
        ;;
    "delete")
        delete_user $2
        ;;
    "")
        show_users
        ;;
    *)
        echo -e "${YELLOW}===== FabGPT 用户管理工具 =====${NC}"
        echo ""
        echo -e "${GREEN}基本用法:${NC}"
        echo "  $0                             - 显示用户列表和详细使用说明"
        echo ""
        echo -e "${GREEN}用户管理:${NC}"
        echo "  $0 create                      - 创建新用户"
        echo "  $0 disable <用户ID>            - 禁用用户"
        echo "  $0 enable <用户ID>             - 启用用户"
        echo "  $0 delete <用户ID>             - 删除用户"
        echo ""
        echo -e "${GREEN}密码管理:${NC}"
        echo "  $0 reset-password <用户ID>     - 重置用户密码"
        echo "    ${YELLOW}• 重置时无密码格式限制${NC}"
        echo "    ${YELLOW}• 用户需要重新登录${NC}"
        echo ""
        echo -e "${GREEN}有效期管理:${NC}"
        echo "  $0 set-expire <用户ID> <日期>  - 设置有效期"
        echo "  $0 set-expire <用户ID>         - 设置为永久有效"
        echo ""
        echo -e "${GREEN}使用示例:${NC}"
        echo "  $0 create                      # 创建新用户"
        echo "  $0 reset-password 7            # 重置ID为7的用户密码"
        echo "  $0 set-expire 4 '2025-12-31 23:59:59'  # 设置有效期"
        echo "  $0 disable 3                   # 禁用用户"
        echo "  $0 enable 3                    # 启用用户"
        echo "  $0 delete 5                    # 删除用户"
        echo ""
        echo -e "${BLUE}提示: 运行 '$0' 查看用户列表和完整使用说明${NC}"
        ;;
esac
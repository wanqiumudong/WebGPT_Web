"""
FabGPT - 半导体器件仿真助手
TCAD_web_MilvusRAG.py
"""
import time
import re
import os
import json
import subprocess
import requests
import signal
import sys
import atexit
import hashlib
import concurrent.futures
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask, request, jsonify, send_from_directory, Response, stream_with_context
from flask_cors import CORS
from werkzeug.utils import secure_filename
import logging
import threading
import traceback

# 多实例配置
INSTANCE_ID = int(os.environ.get('INSTANCE_ID', '1'))
SERVICE_PORT = int(os.environ.get('SERVICE_PORT', '5004'))

# 配置日志 - 实例特定
log_filename = f"logs/tcad_instance_{INSTANCE_ID}.log"
os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler(log_filename), logging.StreamHandler()]
)
logger = logging.getLogger(f"FabGPT-TCAD-Instance-{INSTANCE_ID}")
logging.getLogger('werkzeug').setLevel(logging.WARNING)

# ==================== TOKEN配置常量 ====================
class TokenConfig:
    """统一的max_tokens配置"""
    # 纯决策类 - 只需要简单的选择/判断
    DECISION_ONLY = 32          # 处理模式判断
    
    # 短回答类 - 简短的分析或决策说明
    SHORT_RESPONSE = 256        # RAG使用决策、简短分析
    
    # 中等回答类 - 代码片段、配置建议等
    MEDIUM_RESPONSE = 1024      # 代码修改建议、文件选择决策
    
    # 长回答类 - 详细分析、完整回答
    LONG_RESPONSE = 4096        # 文件分析、仿真结果分析、通用问答

app = Flask(__name__)
CORS(app)
app.config['OUTPUT_FOLDER'] = os.path.abspath("./png_outputs")
app.config['UPLOAD_FOLDER'] = os.path.abspath("./upload_files")
app.config['GENERATE_FOLDER'] = os.path.abspath("./generate_files")  # 新增：代码生成专用文件夹

# 确保目录存在
for folder in [app.config['OUTPUT_FOLDER'], app.config['UPLOAD_FOLDER'], app.config['GENERATE_FOLDER']]:
    os.makedirs(folder, exist_ok=True)

# 项目相关路径
project_path = os.path.abspath("./STDB/NMOS_des")
agent_path = os.path.abspath("./TCAD_agent.py")

# 全局变量 - 按用户分离
user_conversation_histories = {}  # {username: {conversation_id: history}}
uploaded_files = {}
optimization_log = ""
api_cache = {}

# RAG Manager配置 - 使用负载均衡器
RAG_MANAGER_HOST = "10.98.64.22"
RAG_MANAGER_PORT = 5100  # RAG负载均衡器端口
SOCKET_TIMEOUT = 10
is_rag_manager_available = False

def get_available_rag_manager_port():
    """返回RAG负载均衡器端口"""
    return RAG_MANAGER_PORT

# 创建持久化会话
session = requests.Session()
adapter = HTTPAdapter(
    pool_connections=30,
    pool_maxsize=60,
    max_retries=Retry(total=3, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
)
session.mount("https://", adapter)
session.mount("http://", adapter)

# API配置
API_URL = "https://api.siliconflow.cn/v1/chat/completions"
# API_KEY = "sk-irsugzjxawzpmljctfsqjfcwziklolujvvrfznyojlzymksg"
API_KEY = "sk-xcljsjctqjeiwgujfyjuylndklcsgkavtlkwikgcheoqgkoe"
llm_model = "Qwen/Qwen2.5-VL-72B-Instruct"
decision_model = "Qwen/Qwen3-14B"
# decision_model = "Pro/Qwen/Qwen2-7B-Instruct"

system_prompt = """你是FabGPT，基础模型为FabGPT-TCAD模型，由浙江大学开发，拥有扎实的半导体物理和器件原理知识，精通Sentaurus TCAD软件的操作与应用。

当你生成代码时，请遵循以下规则：
1. 始终在代码块的开始使用三个反引号后紧跟语言标识，如```tcl
2. 代码块结束时使用三个反引号```

请始终保持专业、准确和实用性，不提供任何性能预估，对于需要进一步仿真验证的建议要明确说明。
"""

def get_user_conversation_history(username, conversation_id):
    """获取用户特定的对话历史"""
    if username not in user_conversation_histories:
        user_conversation_histories[username] = {}
    if conversation_id not in user_conversation_histories[username]:
        user_conversation_histories[username][conversation_id] = []
    return user_conversation_histories[username][conversation_id]

def set_user_conversation_history(username, conversation_id, history):
    """设置用户特定的对话历史"""
    if username not in user_conversation_histories:
        user_conversation_histories[username] = {}
    user_conversation_histories[username][conversation_id] = history

# 器件代码模板管理系统
class DeviceCodeTemplateManager:
    """器件代码模板管理器"""
    
    def __init__(self):
        self.templates = {
            "finfet": self._get_finfet_template(),
            "mosfet": self._get_mosfet_template(),
            "bjt": self._get_bjt_template()
        }
    
    def _get_finfet_template(self):
        return """
; Reinitializing SDE 
(sde:clear)
; set coordinate system up direction (DF-ISE)
; z: up x: channel length y: gate with
(sde:set-process-up-direction "+z")

;;;;;;;;;;;; device parameters ;;;;;;;;;;
;- structure parameter
;;- channel parameter
(define Lgate (* 1e-3 30))
;(define Lgap 2e-3)
(define Hfin 0.04)
(define Wfin 0.014)
(define Tox (* 2.0 1e-3))
;;;- corner
(define Rfin 3e-3)
(define FinAngle 86)
(define sin_theta (sin (/ (* FinAngle PI) 180)))
(define cos_theta (cos (/ (* FinAngle PI) 180)))

;;--source & drain 
(define Lsource 15e-3)
(define Ldrain Lsource)

;;--STI 
(define Hsti 0.06)
(define Hbulk 0.2)

;;--bulk
(define Xmax (+ Lgate Ldrain))
(define Xmin (* -1 Lsource)) ; source-channel junction zero-coordinate in x-axis

(define Ymax (/ Wfin 1))
(define Ymin (/ Wfin -1))

(define Zmin (- 0 Hsti Hbulk))

;;;;;;;;;;;;;function;;;;;;;;;;;;;;;;;;
(define create_trapzoid 
	(lambda (x0 y0 z0 w h l theta rmater rname r_rounding_top r_rounding_bot direction) 
		
		(define sin_theta (sin (/ (* theta PI) 180)))
		(define cos_theta (cos (/ (* theta PI) 180)))	
		                                         
		(define pbot_left_y (/ w -2))
		(define pbot_right_y (/ w 2))
		
		(define pdy (* h (/ cos_theta sin_theta)))
		
		(define ptop_left_y (+ pbot_left_y pdy))
		(define ptop_right_y (- pbot_right_y pdy))
		
		(define polygon_list (list 
								 (position x0 pbot_left_y z0)
								 (position x0 pbot_right_y z0)
								 (position x0 ptop_right_y (+ z0 h))
								 (position x0 ptop_left_y (+ z0 h))))
		
		(define polygon_1 (sdegeo:create-polygon polygon_list rmater rname))

		;;--sweep polygon
		(sdegeo:sweep polygon_1 (gvector (* l direction) 0 0) 
			(sweep:options "solid" #t "rigid" #f "miter_type" "default"))
		
		(if (> r_rounding_top 0)
			(begin
				(sdegeo:fillet (find-edge-id (position (/ (+ x0 x0 (* direction l)) 2) ptop_right_y (+ z0 h))) r_rounding_top)
				(sdegeo:fillet (find-edge-id (position (/ (+ x0 x0 (* direction l)) 2) ptop_left_y (+ z0 h))) r_rounding_top)))
		
		(if (> r_rounding_bot 0)
			(begin	
				(sdegeo:fillet (find-edge-id (position (/ (+ x0 x0 (* direction l)) 2) pbot_right_y z0)) r_rounding_bot)
				(sdegeo:fillet (find-edge-id (position (/ (+ x0 x0 (* direction l)) 2) pbot_left_y z0)) r_rounding_bot))))) ; function end 

;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;

;--generate source region
(create_trapzoid (* -1 Lsource) 0 0 Wfin Hfin Lsource FinAngle "Silicon" "R.source" Rfin 0 1)
;--generate channel region
(create_trapzoid 0 0 0 Wfin Hfin Lgate FinAngle "Silicon" "R.channel" Rfin 0 1)
(sdegeo:set-default-boolean "BAB")
;--generate gate oxide
(create_trapzoid 0 0 0 (+ Wfin (* 2 (/ Tox sin_theta))) (+ Hfin Tox) Lgate FinAngle "HfO2" "R.gox" (+ Rfin Tox) 0 1)
;--generate drain region
(create_trapzoid Lgate 0 0 Wfin Hfin Ldrain FinAngle "Silicon" "R.drain" Rfin 0 1)
;--generate bulk fin
(create_trapzoid (* -1 Lsource) 0 0 Wfin (* Hsti -1.0) (+ Lsource Ldrain Lgate) FinAngle "Silicon" "R.channel" 0.0 0.0 1)
;--generate STI
(sdegeo:set-default-boolean "BAB")
(sdegeo:create-cuboid (position (* -1 Lsource) Ymin 0) (position (+ Lgate Ldrain) Ymax (* -1 Hsti)) "SiO2" "R.STI")
;--generate bulk silicon 
(sdegeo:create-cuboid (position (* -1 Lsource) Ymin (- 0 Hsti Hbulk)) (position (+ Lgate Ldrain) Ymax (* -1 Hsti)) "Silicon" "R.bulk")

;;;;;;;;;;;;;;; contact defination ;;;;;;;;;;;;;;;
;----define-contact
(sdegeo:define-contact-set "source" 4.0 (color:rgb 0.0 1.0 0.0))
(sdegeo:set-current-contact-set "source")
(sdegeo:set-contact-faces (find-face-id (position (* -1 Lsource) 0 (/ Hfin 2))))

(sdegeo:define-contact-set "drain" 4.0 (color:rgb 0.0 1.0 0.0))
(sdegeo:set-current-contact-set "drain")
(sdegeo:set-contact-faces (find-face-id (position (+ Lgate Ldrain) 0 (/ Hfin 2))))

(sdegeo:define-contact-set "substrate" 4.0 (color:rgb 0.0 1.0 0.0))
(sdegeo:set-current-contact-set "substrate")
(sdegeo:set-contact-faces (find-face-id (position 0 0 (- 0 Hsti Hbulk))))

(define Gate_Region (sdegeo:create-cuboid (position 0 Ymin 0) (position Lgate Ymax (+ Hfin (* 2 Tox))) "Aluminum" "Gate"))

(sdegeo:define-contact-set "gate" 4.0 (color:rgb 0.0 1.0 0.0))
(sdegeo:set-current-contact-set "gate")
(sdegeo:set-contact-boundary-faces Gate_Region)  
(sdegeo:delete-region Gate_Region)

;;;;;;;;;;;;;;; doping profile ;;;;;;;;;;;;;;;
;----define-doping
(define Nchanel 1e17)
(define Nsource_drain 1e20)
(define Nbulk 1e15)

(if (string=? "@Type@" "NMOS")
	(begin
		(define channel_Dopant "BoronActiveConcentration")
		(define source_drain_Dopant "PhosphorusActiveConcentration"))
	(begin
		(define channel_Dopant "PhosphorusActiveConcentration")
		(define source_drain_Dopant "BoronActiveConcentration")))

(sdedr:define-constant-profile "SD_Dop" source_drain_Dopant Nsource_drain)
(sdedr:define-constant-profile-region "Source_Dop_PL" "SD_Dop" "R.source")
(sdedr:define-constant-profile-region "Drain_Dop_PL" "SD_Dop" "R.drain")

;;;;;;;;;;;;;;;;; define mesh ;;;;;;;;;;;;;;;;;
(define fp 0.8)
(define ratio_fp 1)
(define dx (* (/ Lgate 10) fp))
(define dy (* (/ Wfin 10) fp))
(define dz (* (/ Hfin 10) fp))
(define dox (* (/ Tox 5) fp))

;-----mesh 
(sdedr:define-refinement-window "W.Global" "Cuboid"
	(position -1 -1 -1)
	(position 1 1 1))
(sdedr:define-refinement-size "R.Global" (* dx 10) (* dy 10) (* dz 10) (* dx 4) (* dy 4) (* dy 4))
(sdedr:define-refinement-function "R.Global" "MaxLenInt" "Silicon" "SiO2" (* dz 1) (* ratio_fp 1.5))
(sdedr:define-refinement-function "R.Global" "MaxLenInt" "Silicon" "Silicon" (* dz 2) (* ratio_fp 1.5) "DoubleSide")
(sdedr:define-refinement-function "R.Global" "MaxLenInt" "Silicon" "HfO2" (* dox 2) (* ratio_fp 1.5) "DoubleSide")
(sdedr:define-refinement-placement "P.Global" "R.Global" "W.Global")

(sdedr:define-refinement-window "RW.channel" "Cuboid"
	(position 0 (/ Wfin 2) 0)
	(position Lgate (/ Wfin -2) Hfin))
(sdedr:define-refinement-size "RD.channel" (* dx 2) (* dy 4) (* dz 3) (* dx 1) (* dy 2) (* dz 2))
(sdedr:define-refinement-placement "PL.channel" "RD.channel" "RW.channel")

(sdedr:define-refinement-window "RW.source" "Cuboid"
	(position (* -1 Lsource) (/ Wfin 2) 0)
	(position 0 (/ Wfin -2) Hfin))
(sdedr:define-refinement-size "RD.source" (* dx 4) (* dy 5) (* dz 4) (* dx 2) (* dy 3) (* dz 3))
(sdedr:define-refinement-placement "PL.source" "RD.source" "RW.source")

(sdedr:define-refinement-window "RW.drain" "Cuboid"
	(position (* Lgate) (/ Wfin 2) 0)
	(position (+ Ldrain Lgate) (/ Wfin -2) Hfin))
(sdedr:define-refinement-size "RD.drain" (* dx 4) (* dy 5) (* dz 4) (* dx 2) (* dy 3) (* dz 3))
(sdedr:define-refinement-placement "PL.drain" "RD.drain" "RW.drain")

(sde:build-mesh "finfet")
"""

    def _get_mosfet_template(self):
        return """
; 平面MOSFET结构生成模板
(sde:clear)
(sde:set-process-up-direction "+z")

(sdegeo:set-default-boolean "BAB")

(define Tsi 0.1)   ; 硅薄膜厚度
(define Tbox 0.2)  ; 埋氧层厚度
(define Tsub 0.4)  ; 衬底厚度
(define W 1.0)     ; 器件宽度
(define Lgate 0.13); 栅长
(define Tox 2e-3)  ; 栅氧厚度
(define Wspacer 0.1) ; 间隔层宽度
(define Hgate 0.15)   ; 栅极高度

;-create epi silicon
(define EPI_BODY_ID (sdegeo:create-rectangle 
	(position (/ W -2) 0 0) 
	(position (/ W 2) Tsi 0) 
	"Silicon" "R.EPI") )

;-create box oxide 
(sdegeo:create-rectangle 
	(position (/ W -2) Tsi 0) 
	(position 0.5 (+ Tsi Tbox) 0) 
	"Oxide" "R.Box")

;-create substrate
(sdegeo:create-rectangle 
	(position (/ W -2) (+ Tsi Tbox) 0) 
	(position (/ W 2) (+ Tsi Tbox Tsub) 0) 
	"Silicon" "R.Substrate")

;-create gate oxide 
(sdegeo:create-rectangle 
	(position (- 0 Wspacer (/ Lgate 2)) 0 0) 
	(position (+ 0 Wspacer (/ Lgate 2)) (* Tox -1) 0) 
	"Oxide" "R.GateOxide")

;-polysilicon gate 
(sdegeo:create-rectangle 
	(position (/ Lgate -2) (* Tox -1) 0) 
	(position (/ Lgate 2) (* (+ Tox Hgate) -1) 0) 
	"PolySilicon" "R.PolySilicon")

;-spacer 
(sdegeo:create-rectangle 
	(position (- 0 Wspacer (/ Lgate 2)) (* Tox -1) 0) 
	(position (+ 0 Wspacer (/ Lgate 2)) (* (+ Tox Hgate) -1) 0) 
	"Nitride" "R.Spacer")

(sde:info "all")
(generic:get EPI_BODY_ID  "material")
(generic:get EPI_BODY_ID  "region")

; fillet the corner of spacer 
(sdegeo:fillet-2d (find-vertex-id (position (+ 0 Wspacer (/ Lgate 2)) (* (+ Tox Hgate) -1) 0)) 0.02)
(sdegeo:fillet-2d (find-vertex-id (position (- 0 Wspacer (/ Lgate 2)) (* (+ Tox Hgate) -1) 0)) 0.02)

;-------analytic source/drain

(sdedr:define-refeval-window "BaseLine.SourceLDD" 
	"Line"   
	(position (/ W -2) 0.0 0.0)  
	(position (- 0 (/ Lgate 2)) 0.0 0.0 ))

(sdedr:define-gaussian-profile "DD.GaussLDD" 
	"PhosphorusActiveConcentration"
	"PeakPos" 0.0  "PeakVal" 5e17
	"ValueAtDepth"  1e15 "Depth" 0.03
	"Gauss"  "Factor" 0.8)

(sdedr:define-analytical-profile-placement "PL.SourceLDD" 
	"DD.GaussLDD" "BaseLine.SourceLDD" 	"Positive" 	"NoReplace" "Eval")

(sdedr:define-refeval-window "BaseLine.DrainLDD" 
	"Line"   
	(position (/ W 2) 0.0 0.0)  
	(position (+ 0 (/ Lgate 2)) 0.0 0.0 ))

(sdedr:define-analytical-profile-placement "PL.DrainLDD" 
	"DD.GaussLDD" "BaseLine.DrainLDD" 	"Negative" 	"NoReplace" "Eval")

(sdedr:define-refeval-window "BaseLine.Source" 
	"Line"   
	(position (/ W -2) 0.0 0.0)  
	(position (- 0 Wspacer (/ Lgate 2)) 0.0 0.0 ))
 
(sdedr:define-gaussian-profile "DD.GaussSD" 
	"PhosphorusActiveConcentration"
	"PeakPos" 0.0  "PeakVal" 1e20
	"ValueAtDepth"  1e15 "Depth" 0.08
	"Gauss"  "Factor" 0.7)

(sdedr:define-analytical-profile-placement "PL.Source" 
	"DD.GaussSD" "BaseLine.Source" 	"Positive" 	"NoReplace" "Eval")

(sdedr:define-refeval-window "BaseLine.Drain" 
	"Line"   
	(position (/ W 2) 0.0 0.0)  
	(position (+ 0 Wspacer (/ Lgate 2)) 0.0 0.0 ))

(sdedr:define-analytical-profile-placement "PL.Drain" 
	"DD.GaussSD" "BaseLine.Drain" 	"Negative" 	"NoReplace" "Eval")

;------contact defination
(sdegeo:insert-vertex (position (/ (+ (/ W 2) (/ Lgate 2) Wspacer ) -2) 0.0 0.0 ))

(sdegeo:define-contact-set "source"  4.0  (color:rgb 1.0 0.0 0.0 ) "##" )
(sdegeo:set-contact 
	(find-edge-id (position (/ (+ (/ W -2) (/ (+ (/ W 2) (/ Lgate 2) Wspacer ) -2) ) 2) 0.0 0.0 )) "source")

(sdegeo:define-contact-set "drain"  4.0  (color:rgb 0.0 1.0 0.0 ) "##" )
(sdegeo:set-contact 
	(find-edge-id (position (/ (+ (/ W 2) (/ Lgate 2) Wspacer ) 2) 0.0 0.0 )) "drain")
 
(sdegeo:define-contact-set "substrate"  4.0  (color:rgb 1.0 0.0 0.0 ) "##" )
(sdegeo:set-contact 
	(find-edge-id (position 0.0 (+ Tsi Tbox Tsub) 0.0 )) "substrate")

(sdegeo:define-contact-set "gate"  4.0  (color:rgb 1.0 0.0 0.0 ) "##" )
(sdegeo:set-contact (find-body-id (position 0.0 (/ (+ Tox Hgate Tox) -2) 0.0 )) "gate" "remove")

;-----mesh
;Axis-Aliged Mesh

(sdedr:define-refeval-window 
	"RW.Global" "Rectangle" 
	(position -1000 -1000 0)
	(position  1000  1000 0) 
	)
(sdedr:define-refinement-size "RD.Global" 
	0.05  0.05 
	0.002 0.002 )
 
(sdedr:define-refinement-function "RD.Global" 
   "DopingConcentration" "MaxTransDiff" 1.0)
(sdedr:define-refinement-function "RD.Global" 
	"MaxLenInt" "R.EPI" "R.GateOxide" 5e-4 1.5  "DoubleSide" "UseRegionNames"
)
(sdedr:define-refinement-function "RD.Global" 
	"MaxLenInt" "Contact" "Silicon" 5e-4 1.5  
)
(sdedr:define-refinement-placement "RPL.Global" 
	"RD.Global"  "RW.Global")

(sdedr:define-refeval-window 
	"RW.Channel" "Rectangle" 
	(position (/ Lgate -2) 0 0)
	(position (/ Lgate 2)  Tsi 0) 
	)

(sdedr:define-refinement-size "RD.Channel" 
	(/ Lgate 15) 1.0
	0.005 0.005 )
(sdedr:define-refinement-placement "RPL.Channel" 
	"RD.Channel"  "RW.Channel")

(sde:build-mesh "mosfet")
"""

    def _get_bjt_template(self):
        return """
; BJT结构生成模板
; Reinitializing SDE 
(sde:clear)
; set coordinate system up direction 
(sde:set-process-up-direction "-x")

; Emulation Domain
(define Ymax 2.2) ; Width
(define Zmax 1.2) ; Depth
(sdepe:define-pe-domain (list 0.0 0.0 Ymax Zmax))

; Substrate definition
(define Nasub 1e16)  ; Boron concentration
(define Xsub 2.0)    ; Initial Substrate Height
(sdepe:add-substrate "material" "Silicon" "thickness" Xsub "region" "Substrate")
(sdepe:doping-constant-placement "DopSub" 
 "BoronActiveConcentration" Nasub "Substrate")

; Subcollector implant mask
(define Yb1 0.2)  ; Beginning of base window
(define Yb2 1.2)  ; End of base window
(define Yc1 1.5)  ; Beginning of collector contact window
(define Yc2 2.0)  ; End of collector contact window
(define Zc  0.8)  ; Depth of contact

(sdepe:generate-mask "SUBC" (list (list Yb1 0.0 Yc2 Zc)) )

(define Tre 0.5)
(sdepe:pattern "mask" "SUBC" "polarity" "dark" "type" "aniso"
               "material" "Resist"  "thickness" Tre )

; Subcollector implant
(define LatDiff 0.02)
(sdedr:define-gaussian-profile 
	"SubCol" "PhosphorusActiveConcentration" 
	"PeakPos" 0.0 "PeakVal" 5e+19 
	"ValueAtDepth" Nasub "Depth" 0.5 
	"Gauss" "Length" LatDiff)

(sdepe:implant "SubCol")
(sdepe:remove "material" "Resist")

; Deposit Silicon epi layer
(define Tepi 0.3)
(define Ndepi 5e16)
(define SiEpi (sdepe:depo "material" "Silicon"  "thickness" Tepi "type" "iso"))
(sde:add-material SiEpi "Silicon" "SiEpi")
(sdepe:doping-constant-placement "DopEpi" "ArsenicActiveConcentration" 
  Ndepi "SiEpi")

; Isolation mask
(sdepe:generate-mask "ISO" ; Protection mask
(list (list Yb1 0.0 Yb2 Zc)
      (list Yc1 0.0 Yc2 Zc) ))
(sdepe:pattern "mask" "ISO" "polarity" "light" "type" "aniso"
               "material" "Resist"  "thickness" Tre )
(sdepe:etch-material "material" "Silicon" "depth" Tepi ) ; Trench etching
(sdepe:remove "material" "Resist")

; Fill trench
(define Xtop (sde:min-x (get-body-list)))
(sdepe:fill-device "material" "Oxide" "height" (- Xtop 0.1) )

; CMP polish 
(sdepe:polish-device "thickness" 0.1)

; Deposit screening oxide
(define Tscreen 0.03)
(sdepe:depo "material" "Oxide" "thickness" Tscreen)

; Collector contact implant mask
(sdepe:generate-mask "COL" (list (list Yc1 0.0 Yc2 Zc)))
(sdepe:pattern "mask" "COL" "polarity" "dark" "type" "aniso"
               "material" "Resist" "thickness" Tre)

; Collector contact implant
(sdedr:define-gaussian-profile "ColCont" "ArsenicActiveConcentration" 
	"PeakPos" 0.0 "PeakVal" 5e+19 
	"ValueAtDepth" 1e+17 "Depth" 0.5 
	"Gauss" "Length" LatDiff
)
(sdepe:implant "ColCont")
(sdepe:remove "material" "Resist")

; Base implant mask
(sdepe:generate-mask "BAS" (list (list Yb1 0.0 Yb2 Zc)))
(sdepe:pattern "mask" "BAS" "polarity" "dark" "type" "aniso"
               "material" "Resist" "thickness" Tre)

; Base implant
(define Tbase 0.2)
(define Nabase 3e18)
(sdedr:define-gaussian-profile 
	"Base" "BoronActiveConcentration" 
	"PeakPos" 0.0 "PeakVal" Nabase 
	"ValueAtDepth" Ndepi "Depth" Tbase 
	"Gauss" "Length" LatDiff
)
(sdepe:implant "Base")
(sdepe:remove "material" "Resist")

; Emitter implant mask
(define Ye1 0.9)
(define Ye2 1.1)
(define Ze  (* 0.7 Zc))
(sdepe:generate-mask "EMIT" (list  (list Ye1 0.0 Ye2 Ze)))
(sdepe:pattern "mask" "EMIT" "polarity" "dark" "type" "aniso"
               "material" "Resist" "thickness" Tre)

; Emitter implant
(define Temit 0.1)
(sdedr:define-gaussian-profile 
	"Emitter" "PhosphorusActiveConcentration" 
	"PeakPos" 0.0 "PeakVal" 1e+20 
	"ValueAtDepth" 1.0e18 "Depth" Temit 
	"Gauss" "Length" LatDiff
)
(sdepe:implant "Emitter")
(sdepe:remove "material" "Resist")

; Emitter and collector contact holes mask
(define reset 0.05)
(sdepe:generate-mask "PCH" 
 (list (list (+ Ye1 reset) 0.0 (- Ye2 reset) (- Ze reset))
       (list (+ Yc1 reset) 0.0 (- Yc2 reset) (- Zc reset)))) 
(sdepe:pattern "mask" "PCH" "polarity" "dark" "type" "aniso"
               "material" "Resist" "thickness" Tre)

; Etching emitter and collector contact holes
(sdepe:etch-material "material" "Oxide" "depth" Tscreen)
(sdepe:remove "material" "Resist")

; Deposit PolySi
(define Tpoly 0.1)
(define POLYSI (sdepe:depo "material" "PolySi" "thickness" Tpoly))
(sde:add-material POLYSI "PolySi" "Poly")
(sdepe:doping-constant-placement "DopPoly" "ArsenicActiveConcentration" 1e20 
  "Poly")

; Poly mask
(sdepe:generate-mask "POL"  (list  (list Yc1 0.0 Yc2 Zc) (list Ye1 0.0 Ye2 Ze)))
(sdepe:pattern "mask" "POL"  "polarity" "light" "type" "aniso"
               "material" "Resist" "thickness" Tre)

; Etching poly
(sdepe:etch-material "material" "PolySi" "depth" Tpoly)
(sdepe:remove "material" "Resist")

; Fill
(define Xtop (- (sde:min-x (get-body-list)) 0.05))
(sdepe:fill-device "material" "Oxide" "height" Xtop )

; Metal contact holes mask
(define Ybc1 (+ Yb1 0.1))
(define Ybc2 (+ Ybc1 0.4))
(sdepe:generate-mask "MET1" 
 (list (list Yc1  0.0 Yc2  Zc)
       (list Ye1  0.0 Ye2  Ze)
       (list Ybc1 0.0 Ybc2 Ze))) 
(sdepe:pattern "mask" "MET1" "polarity" "dark" "type" "aniso"
               "material" "Resist" "thickness" Tre)

; Etching metal contact holes
(sdepe:etch-material "material" "Oxide" "depth" (+ Tpoly 0.05))
(sdepe:etch-material "material" "Oxide" "depth" Tscreen)
(sdepe:remove "material" "Resist")

; Fill contact holes
(sdepe:fill-device "material" "Metal")

(sde:separate-lumps)
; Contact definitions 
(sdegeo:set-contact (find-face-id (position 0.0 0.01 0.01)) "substrate")  

(define BCID (find-body-id 
  (position  (+ Xtop 0.01) (* 0.5 (+ Ybc1 Ybc2)) (* 0.5 Ze))))                  
(sdegeo:set-contact BCID "base" "remove")  

(define ECID (find-body-id 
  (position (+ Xtop 0.01) (* 0.5 (+ Ye1 Ye2)) (* 0.5 Ze))))                  
(sdegeo:set-contact ECID "emitter" "remove")  

(define CCID (find-body-id 
  (position (+ Xtop 0.01) (* 0.5 (+ Yc1 Yc2)) (* 0.5 Zc))))                  
(sdegeo:set-contact CCID "collector" "remove")            

; Global
(define Xbot (sde:max-x (get-body-list)))
(define Xtop (sde:min-x (get-body-list)))
(sdedr:define-refeval-window "All_RW" "Cuboid" 
  (position  Xtop 0 0) (position Xbot Ymax Zmax) )
(sdedr:define-refinement-size "All_RD" 
  (/ (- Xbot Xtop) 08.0) (/ Ymax  8.0) (/ Zmax  8.0)
  (/ (- Xbot Xtop) 16.0) (/ Ymax 16.0) (/ Zmax 16.0))
(sdedr:define-refinement-function "All_RD" 
  "DopingConcentration" "MaxTransDiff" 1)
(sdedr:define-refinement-placement "All_PL" "All_RD" "All_RW" )

; Top region
(sdedr:define-refeval-window "Top_RW" "Cuboid" 
  (position -2.3 Yb1 0) (position -1.25 Yc2 Zc))
(sdedr:define-refinement-size "Top_RD" 
  (/ (- Xbot Xtop) 16.0) (/ Ymax 16.0) (/ Zmax 16.0)
  (/ (- Xbot Xtop) 32.0) (/ Ymax 32.0) (/ Zmax 32.0))
(sdedr:define-refinement-function "Top_RD" 
  "DopingConcentration" "MaxTransDiff" 1)
(sdedr:define-refinement-placement "Top_PL" "Top_RD" "Top_RW" )

; Base region
(sdedr:define-refeval-window "Base_RW" "Cuboid" 
  (position (- (- Xsub) Tepi) Yb1 0.0) (position (- Xsub) Yb2 Zc ))
(sdedr:define-refinement-size "Base_RD" 
  (/ Tepi 8.0) (/ (+ Yb2 Yb1)  8.0) (/ Zc  8.0)
  (/ Tepi 32.0) (/ (+ Yb2 Yb1) 32.0) (/ Zc 32.0))
(sdedr:define-refinement-function "Base_RD" 
  "DopingConcentration" "MaxTransDiff" 1)
(sdedr:define-refinement-placement "Base_PL" "Base_RD" "Base_RW" )

; Active region
(sdedr:define-refeval-window "Active_RW" "Cuboid" 
  (position -2.3 0.85 0.0) (position -2.15 1.15 0.6))
(sdedr:define-refinement-size "Active_RD" 
  0.02 0.02 0.08
  0.01 0.01 0.04)
(sdedr:define-refinement-function "Active_RD" 
  "DopingConcentration" "MaxTransDiff" 1)
(sdedr:define-refinement-placement "Active_PL" "Active_RD" "Active_RW" )

; Poly
(sdedr:define-refinement-size "Poly_RD" 
  (/ Tpoly 8.0) 99 99 
  (/ Tpoly 9.0) 66 66
)
(sdedr:define-refinement-material "Poly_PL" "Poly_RD" "PolySi" ) 

; Meshing the device
(sde:build-mesh "bjt")
"""

    def get_template(self, device_type):
        """获取指定器件类型的代码模板"""
        return self.templates.get(device_type, self.templates["mosfet"])

def validate_generated_code(code, device_type, expected_params):
    """验证生成的代码质量"""
    
    validation_rules = {
        "finfet": {
            "required_structures": ["FinChannel", "FinSource", "FinDrain", "STI", "GateOx", "PolyGate"],
            "required_functions": ["sdegeo:create-cuboid", "sdedr:define-refinement-size"],
            "mesh_density_check": "0.0005",  # 至少0.5nm
            "dimension_check": {"fin_width": (0.005, 0.020), "gate_length": (0.010, 0.050)}
        },
        "mosfet": {
            "required_structures": ["SourceRegion", "DrainRegion", "ChannelRegion", "GateOxide"],
            "required_functions": ["sdegeo:create-rectangle", "MaxLenInt"],
            "mesh_density_check": "0.001",   # 至少1nm
            "dimension_check": {"gate_length": (0.030, 0.500), "gate_width": (0.100, 10.0)}
        },
        "bjt": {
            "required_structures": ["Emitter", "Base", "Collector", "Substrate"],
            "required_functions": ["DopingConcentration", "MaxTransDiff"],
            "mesh_density_check": "0.002",   # 至少2nm
            "dimension_check": {"emitter_width": (0.5, 5.0), "base_thickness": (0.050, 0.500)}
        }
    }
    
    rules = validation_rules.get(device_type, validation_rules["mosfet"])
    
    # 检查必需结构
    missing_structures = [struct for struct in rules["required_structures"] 
                         if struct not in code]
    
    if missing_structures:
        return {
            "valid": False, 
            "message": f"缺少关键结构定义: {', '.join(missing_structures)}"
        }
    
    # 检查网格密度
    if rules["mesh_density_check"] not in code:
        return {
            "valid": False,
            "message": f"{device_type}器件需要更精细的网格密度(≤{rules['mesh_density_check']}um)"
        }
    
    # 检查尺寸合理性
    for param, (min_val, max_val) in rules["dimension_check"].items():
        if param in expected_params:
            try:
                value = float(expected_params[param])
                if not (min_val <= value <= max_val):
                    return {
                        "valid": False,
                        "message": f"{param}尺寸({value}um)超出合理范围({min_val}-{max_val}um)"
                    }
            except:
                pass
    
    # 检查接触定义
    contact_keywords = ["sdegeo:define-contact-set", "sdegeo:set-contact-faces"]
    if not any(keyword in code for keyword in contact_keywords):
        return {
            "valid": False,
            "message": "缺少完整的接触定义"
        }
    
    return {"valid": True, "message": "代码结构验证通过"}


# ==================== 统一决策Agent ====================
def unified_decision_agent(user_message, conversation_id, classified_files, config_id='default', username='anonymous'):
    """
    统一的决策agent，一次性返回所有决策信息
    返回格式：
    {
        "mode": "simulation" | "generate" | "qna",
        "device_type": "finfet" | "mosfet" | "bjt" | "others",
        "files_used": {...},
        "code_type": "sde" | "sdevice",
        "rag_config": {...},
        "reason": "决策理由"
    }
    """
    global uploaded_files  # 添加全局变量声明
    logger.info(f"启动统一决策agent...用户={username}, 对话={conversation_id}")
    
    # 获取对话历史上下文
    recent_history = ""
    user_history = get_user_conversation_history(username, conversation_id)
    if len(user_history) > 0:
        recent_msgs = user_history[:-1][-3:]
        for msg in recent_msgs:
            if msg["role"] == "user":
                recent_history += f"用户之前的提问: {msg['content']}\n"
    
    # 构建文件上下文
    files_context = ""
    if any(classified_files.values()):
        file_count = sum(len(files) for files in classified_files.values())
        files_context = f"用户上传了{file_count}个文件,"
        for file_type in ["sde", "sdevice", "sprocess", "unknown"]:
            if classified_files[file_type]:
                file_names = ", ".join([f['name'] for f in classified_files[file_type]])
                files_context += f"其中{len(classified_files[file_type])}个{file_type}文件: {file_names};"
    
    # 构建统一决策prompt
    unified_prompt = f"""
你是FabGPT的统一决策专家。请基于用户消息、对话历史和文件信息，做出完整的处理决策。

用户当前消息: "{user_message}"
对话历史: {recent_history}
文件信息: {files_context}

请判断处理模式、器件类型并返回完整的决策信息：

处理模式选项：
1. simulation - 执行TCAD仿真操作
2. generate - 生成TCAD代码（sde结构代码或sdevice仿真代码）
3. qna - 问答模式（包括文件分析）

器件类型识别：
- finfet: 鳍式场效应管
- mosfet: 平面MOSFET
- bjt: 双极型晶体管
- others: 其他器件类型

判断标准：
- 只有当用户明确要求运行仿真或使用"开始仿真"等指令时，选择simulation
- 如果用户请求生成、编写、构建TCAD代码，选择generate
- 其他情况（包括文件分析、问答）选择qna

对于不同模式，需要额外决策：

**simulation模式**：
- 需要选择sde文件（结构文件）和sdevice/sprocess文件（仿真文件）
- 优先选择用户明确提到的文件，否则选择最新上传的相应类型文件

**generate模式**：
- 需要判断生成代码类型：sde（器件结构定义）或sdevice（仿真设置）
- 需要判断是否使用RAG知识库来辅助代码生成
- 基于用户描述的需求内容判断，简单的代码一般不需要知识库
- 生成用于RAG搜索的embedding_word（与代码生成相关的关键词）

**qna模式**：
- 需要判断是否使用RAG知识库
- 如果是极简单问题、问候语，通常不需要知识库
- 如果用户强调分析特定文件，不需要知识库
- 复杂专业问题需要知识库，top_k范围3-15
- 生成用于RAG搜索的embedding_word（关键词，英文形式，例如sdegeo:create-rectangle）

请返回JSON格式的完整决策：
```json
{{
    "mode": "simulation|generate|qna",
    "device_type": "finfet|mosfet|bjt|others",
    "files_used": {{
        "sde_file": "文件名（simulation模式）",
        "sdevice_file": "文件名（simulation模式）", 
        "files_to_analyze": ["文件名列表（qna模式文件分析）"]
    }},
    "code_type": "sde|sdevice",
    "rag_config": {{
        "use_rag": true|false,
        "top_k": 3-15,
        "embedding_word": "根据器件类型和用户需求优化的RAG搜索关键词"
    }},
    "reason": "详细的决策理由，包括器件类型识别依据"
}}
```

**特别说明embedding_word生成规则：**
- finfet相关: "sdegeo:create-cuboid finfet fin structure gate-all-around 3D mesh refinement"
- mosfet相关: "sdegeo:create-rectangle planar mosfet gate oxide source drain 2D"  
- bjt相关: "bipolar junction emitter collector base vertical doping profile"
- 结合用户提到的具体参数和技术要求

只返回JSON格式结果，不要任何其他内容。
"""
    
    try:
        # 调用API获取统一决策
        response = call_api_streaming(system_prompt, unified_prompt, max_tokens=TokenConfig.MEDIUM_RESPONSE, model=decision_model, temperature=0.1)
        decision_text = extract_complete_response(response).strip()
        
        # 解析JSON响应
        json_match = re.search(r'```json\s*(.+?)\s*```', decision_text, re.DOTALL)
        if json_match:
            decision_json = json.loads(json_match.group(1))
        else:
            # 尝试直接解析
            decision_json = json.loads(decision_text)
        
        # 验证和补全决策结果
        validated_decision = validate_and_complete_decision(decision_json, classified_files, user_message, config_id)
        
        logger.info(f"统一决策结果: 模式={validated_decision['mode']}, 器件类型={validated_decision['device_type']}, 理由={validated_decision['reason']}")
        return validated_decision
        
    except Exception as e:
        logger.error(f"统一决策agent出错: {str(e)}")

def validate_and_complete_decision(decision_json, classified_files, user_message, config_id):
    """验证和补全决策结果"""
    # 设置默认值
    decision = {
        "mode": decision_json.get("mode", "qna"),
        "device_type": decision_json.get("device_type", "mosfet"),
        "files_used": decision_json.get("files_used", {}),
        "code_type": decision_json.get("code_type", "sde"),
        "rag_config": decision_json.get("rag_config", {}),
        "reason": decision_json.get("reason", "默认决策")
    }
    
    # 验证模式
    if decision["mode"] not in ["simulation", "generate", "qna"]:
        decision["mode"] = "qna"
    
    # 验证器件类型
    if decision["device_type"] not in ["finfet", "mosfet", "bjt", "others"]:
        decision["device_type"] = "mosfet"
    
    # 补全文件选择逻辑
    if decision["mode"] == "simulation":
        # 为仿真模式补全文件选择
        files_used = decision["files_used"]
        
        # 如果分类文件为空但用户明确提到文件，尝试从已上传文件中寻找
        if (not any(classified_files.values()) and 
            any(keyword in user_message for keyword in ["文件", "基于", "上传", "以上", "前面", "之前"])):
            # 尝试从用户对话上下文中获取文件
            logger.info("检测到用户提及文件但classified_files为空，尝试从全局上传文件中查找")
            
            # 从全局uploaded_files中查找该用户会话的文件
            user_conversation_key = f"{username}_{conversation_id}"
            try:
                if user_conversation_key in uploaded_files:
                    logger.info(f"从全局uploaded_files中找到用户文件: {list(uploaded_files[user_conversation_key].keys())}")
                    # 重新分类文件
                    classified_files = classify_uploaded_files(uploaded_files[user_conversation_key])
                    logger.info(f"重新分类结果: sde={len(classified_files.get('sde', []))}, sdevice={len(classified_files.get('sdevice', []))}")
                else:
                    # 如果当前会话没有文件，尝试从磁盘重新加载文件
                    logger.info(f"内存中无会话文件记录，尝试从磁盘重新加载: {user_conversation_key}")
                    user_conversation_dir = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(username), secure_filename(conversation_id))
                    
                    if os.path.exists(user_conversation_dir):
                        # 重新加载磁盘文件到内存
                        loaded_files = {}
                        for file_name in os.listdir(user_conversation_dir):
                            file_path = os.path.join(user_conversation_dir, file_name)
                            if os.path.isfile(file_path):
                                # 从文件名中提取原始名称和时间戳
                                # 格式：originalname_timestamp.ext
                                parts = file_name.rsplit('_', 1)
                                if len(parts) == 2 and parts[1].replace('.', '').isdigit():
                                    # 有时间戳
                                    name_part = parts[0]
                                    ext_part = parts[1].split('.', 1)
                                    if len(ext_part) == 2:
                                        original_name = f"{name_part}.{ext_part[1]}"
                                    else:
                                        original_name = name_part
                                else:
                                    # 没有时间戳，直接使用文件名
                                    original_name = file_name
                                
                                file_content = read_file(file_path)
                                file_type_result = model_classify_file(original_name)
                                file_type = file_type_result.get("type", "unknown")
                                
                                file_info = {
                                    "name": original_name,
                                    "saved_name": file_name,
                                    "path": file_path,
                                    "content": file_content,
                                    "type": file_type,
                                    "reason": file_type_result.get("reason", ""),
                                    "original_size": len(file_content),
                                    "processed_size": len(file_content),
                                    "upload_time": int(os.path.getmtime(file_path))
                                }
                                loaded_files[original_name] = file_info
                        
                        if loaded_files:
                            logger.info(f"从磁盘成功重新加载文件: {list(loaded_files.keys())}")
                            uploaded_files[user_conversation_key] = loaded_files
                            classified_files = classify_uploaded_files(loaded_files)
                            logger.info(f"磁盘文件重新分类结果: sde={len(classified_files.get('sde', []))}, sdevice={len(classified_files.get('sdevice', []))}")
                        else:
                            logger.info(f"磁盘目录 {user_conversation_dir} 中无有效文件")
                    else:
                        # 尝试从用户的其他会话中查找最近的文件
                        logger.info(f"磁盘目录不存在，尝试从用户的其他会话中查找")
                        user_files = {}
                        latest_timestamp = 0
                        latest_session_key = None
                        
                        for session_key in uploaded_files:
                            try:
                                if session_key.startswith(f"{username}_"):
                                    session_files = uploaded_files[session_key]
                                    if session_files:
                                        # 找到最新的文件会话
                                        for file_info in session_files.values():
                                            upload_time = file_info.get('upload_time', 0)
                                            if upload_time > latest_timestamp:
                                                latest_timestamp = upload_time
                                                latest_session_key = session_key
                            except Exception as inner_e:
                                logger.error(f"检查会话键 {session_key} 时出错: {inner_e}")
                                continue
                        
                        if latest_session_key:
                            logger.info(f"找到用户最近的文件会话: {latest_session_key}，文件数: {len(uploaded_files[latest_session_key])}")
                            # 将最近会话的文件复制到当前会话
                            uploaded_files[user_conversation_key] = uploaded_files[latest_session_key].copy()
                            # 重新分类文件
                            classified_files = classify_uploaded_files(uploaded_files[user_conversation_key])
                            logger.info(f"跨会话文件继承成功: sde={len(classified_files.get('sde', []))}, sdevice={len(classified_files.get('sdevice', []))}")
                        else:
                            logger.warning(f"全局uploaded_files和磁盘中均未找到用户 {username} 的任何文件")
            except Exception as e:
                logger.error(f"文件恢复过程中出错: {e}")
                # 继续执行，不中断决策流程
            
        # 选择SDE文件
        if not files_used.get("sde_file") and classified_files.get("sde"):
            files_used["sde_file"] = classified_files["sde"][0]["name"]
        
        # 选择仿真文件
        if not files_used.get("sdevice_file"):
            if classified_files.get("sdevice"):
                files_used["sdevice_file"] = classified_files["sdevice"][0]["name"]
            elif classified_files.get("sprocess"):
                files_used["sdevice_file"] = classified_files["sprocess"][0]["name"]
        
        # 转换为实际的文件对象
        final_files = {}
        if files_used.get("sde_file"):
            for file_info in classified_files.get("sde", []):
                if file_info["name"] == files_used["sde_file"]:
                    final_files["sde"] = file_info
                    break
        
        if files_used.get("sdevice_file"):
            for file_type in ["sdevice", "sprocess"]:
                for file_info in classified_files.get(file_type, []):
                    if file_info["name"] == files_used["sdevice_file"]:
                        final_files[file_type] = file_info
                        break
        
        decision["files_used"] = final_files
    
    elif decision["mode"] == "qna":
        # 补全RAG配置
        rag_config = decision["rag_config"]
        rag_config.setdefault("use_rag", True)
        rag_config.setdefault("top_k", 5)
        rag_config.setdefault("embedding_word", user_message)
        
        # 验证RAG配置
        if config_id == 'none' or config_id == 'None':
            rag_config["use_rag"] = False
        
        if rag_config["top_k"] < 3:
            rag_config["top_k"] = 3
        elif rag_config["top_k"] > 15:
            rag_config["top_k"] = 15
        
        # 处理文件分析
        if any(classified_files.values()):
            file_keywords = ["分析", "查看", "解读", "介绍", "读取", "显示", "查询", "文件", "内容"]
            if any(kw in user_message for kw in file_keywords):
                files_to_analyze = decision["files_used"].get("files_to_analyze", [])
                if not files_to_analyze:
                    # 自动选择最新文件
                    for file_type in ["sde", "sdevice", "sprocess", "unknown"]:
                        if classified_files[file_type]:
                            newest_file = max(classified_files[file_type], key=lambda x: x.get("upload_time", 0))
                            files_to_analyze = [newest_file["name"]]
                            break
                decision["files_used"]["files_to_analyze"] = files_to_analyze
    
    # 验证代码类型
    if decision["mode"] == "generate":
        if decision["code_type"] not in ["sde", "sdevice"]:
            decision["code_type"] = "sde"
    
    return decision

# 处理模式函数
def handle_simulation_mode(user_message, conversation_id, files_used, request_id=None, username='anonymous'):
    """处理仿真模式的流式响应"""
    logger.info("检测到仿真请求")
    
    # 检查中止状态
    if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
        app.aborted_streams.discard(request_id)
        abort_chunk = {"chunk": "\n\n[回答已中止]", "is_complete": True, "aborted": True}
        yield f"data: {json.dumps(abort_chunk)}\n\n"
        return
    
    conv_paths = get_conversation_paths(conversation_id, username)
    
    if "sde" not in files_used or not files_used["sde"]:
        error_chunk = {"chunk": "未找到合适的结构文件，请上传包含sde关键字的文件。", "is_complete": True}
        yield f"data: {json.dumps(error_chunk)}\n\n"
        return
    
    if "sdevice" not in files_used and "sprocess" not in files_used:
        error_chunk = {"chunk": "未找到合适的仿真文件，请上传包含sdevice或sprocess关键字的文件。", "is_complete": True}
        yield f"data: {json.dumps(error_chunk)}\n\n"
        return
    
    selected_files_info = f"结构文件: {files_used['sde']['name']}"
    if "sdevice" in files_used:
        selected_files_info += f", 仿真文件: {files_used['sdevice']['name']}"
    elif "sprocess" in files_used:
        selected_files_info += f", 工艺文件: {files_used['sprocess']['name']}"
    
    file_selection_chunk = {
        "chunk": f"已选择以下文件进行仿真:\n{selected_files_info}\n\n正在准备仿真...\n",
        "is_complete": False
    }
    yield f"data: {json.dumps(file_selection_chunk)}\n\n"
    
    # 检查中止状态
    if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
        app.aborted_streams.discard(request_id)
        abort_chunk = {"chunk": "\n\n[回答已中止]", "is_complete": True, "aborted": True}
        yield f"data: {json.dumps(abort_chunk)}\n\n"
        return
    
    structure = files_used["sde"]
    simulation = files_used.get("sdevice", files_used.get("sprocess"))

    structure_chunk = {"chunk": "正在分析结构文件...\n", "is_complete": False}
    yield f"data: {json.dumps(structure_chunk)}\n\n"
    
    # 检查中止状态
    if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
        app.aborted_streams.discard(request_id)
        abort_chunk = {"chunk": "\n\n[回答已中止]", "is_complete": True, "aborted": True}
        yield f"data: {json.dumps(abort_chunk)}\n\n"
        return
    
    sde_analysis = ""
    for chunk in analyze_tcad_files_streaming(structure["content"], structure["type"], request_id):
        # 检查中止状态
        if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
            app.aborted_streams.discard(request_id)
            abort_chunk = {"chunk": "\n\n[回答已中止]", "is_complete": True, "aborted": True}
            yield f"data: {json.dumps(abort_chunk)}\n\n"
            return
        sde_analysis += chunk

    simulation_chunk = {"chunk": "正在分析仿真文件...\n", "is_complete": False}
    yield f"data: {json.dumps(simulation_chunk)}\n\n"
    
    # 检查中止状态
    if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
        app.aborted_streams.discard(request_id)
        abort_chunk = {"chunk": "\n\n[回答已中止]", "is_complete": True, "aborted": True}
        yield f"data: {json.dumps(abort_chunk)}\n\n"
        return
    
    sim_analysis = ""
    for chunk in analyze_tcad_files_streaming(simulation["content"], simulation["type"], request_id):
        # 检查中止状态
        if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
            app.aborted_streams.discard(request_id)
            abort_chunk = {"chunk": "\n\n[回答已中止]", "is_complete": True, "aborted": True}
            yield f"data: {json.dumps(abort_chunk)}\n\n"
            return
        sim_analysis += chunk

    with open(conv_paths["analysis_output_file"], 'w', encoding='utf-8') as f:
        f.write("TCAD Structure and Simulation Analysis\n" + "=" * 40 + "\n\n")
        f.write("结构文件分析:\n" + sde_analysis + "\n\n")
        f.write("仿真文件分析:\n" + sim_analysis + "\n\n")

    code_change_chunks = []
    for chunk in generate_code_changes_streaming(simulation["content"], user_message, request_id):
        # 检查中止状态
        if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
            app.aborted_streams.discard(request_id)
            abort_chunk = {"chunk": "\n\n[回答已中止]", "is_complete": True, "aborted": True}
            yield f"data: {json.dumps(abort_chunk)}\n\n"
            return
        code_change_chunks.append(chunk)

    code_report = "".join(code_change_chunks)
    logger.info("生成的代码块:\n" + code_report)

    code_chunk = {"chunk": "正在执行仿真...\n", "is_complete": False}
    yield f"data: {json.dumps(code_chunk)}\n\n"

    # 检查中止状态
    if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
        app.aborted_streams.discard(request_id)
        abort_chunk = {"chunk": "\n\n[回答已中止]", "is_complete": True, "aborted": True}
        yield f"data: {json.dumps(abort_chunk)}\n\n"
        return

    combined_code = code_report + "\n\n" + simulation["content"]
    save_to_tcl_file(conv_paths["output_file_path"], combined_code)

    try:
        result = subprocess.run(
            ["python", agent_path, project_path, structure["path"], conv_paths["output_file_path"],
             "--output_dir", conv_paths["output_dir"]],
            check=True, text=True, capture_output=True
        )
        logger.info("仿真输出：" + result.stdout)
        
        # 检查中止状态
        if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
            app.aborted_streams.discard(request_id)
            abort_chunk = {"chunk": "\n\n[回答已中止]", "is_complete": True, "aborted": True}
            yield f"data: {json.dumps(abort_chunk)}\n\n"
            return
        
        sim_result_chunk = {"chunk": "\n\n仿真已完成，正在分析结果...\n", "is_complete": False}
        yield f"data: {json.dumps(sim_result_chunk)}\n\n"
        
    except subprocess.CalledProcessError as e:
        logger.error(f"仿真运行出错：{e.stderr}")
        error_chunk = {"chunk": f"\n\n仿真运行出错: {e.stderr}\n", "is_complete": True}
        yield f"data: {json.dumps(error_chunk)}\n\n"
        return

    output_result = read_file(conv_paths["output_result_path"])
    processed_log = process_sentaurus_log(
        conv_paths["output_log_path"], 
        os.path.join(conv_paths["output_dir"], f"processed_log_{conversation_id}.txt")
    )

    for chunk in analyze_simulation_results_streaming(sde_analysis, sim_analysis, output_result, processed_log, optimization_log, request_id):
        # 检查中止状态
        if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
            app.aborted_streams.discard(request_id)
            abort_chunk = {"chunk": "\n\n[回答已中止]", "is_complete": True, "aborted": True}
            yield f"data: {json.dumps(abort_chunk)}\n\n"
            return
        analysis_chunk = {"chunk": chunk, "is_complete": False}
        yield f"data: {json.dumps(analysis_chunk)}\n\n"

    backend_url = "http://10.98.64.22:5002"
    # 使用用户分离的路径
    user_conv_key = conv_paths.get("user_conv_key", conversation_id)
    image_url = f"{backend_url}/static/outputs/{user_conv_key}/outputs_node3_{conversation_id}.png"
    image_html = f'\n\n<img src="{image_url}" alt="Simulation Result" width="600">'
    
    image_chunk = {"chunk": image_html, "is_complete": True}
    yield f"data: {json.dumps(image_chunk)}\n\n"

def handle_file_analysis_mode(user_message, conversation_id, files_to_analyze, conversation_files, request_id=None, username='anonymous'):
    """处理文件分析模式的流式响应"""
    logger.info(f"启动智能文件分析流程")
    
    # 检查中止状态
    if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
        app.aborted_streams.discard(request_id)
        abort_chunk = {"chunk": "\n\n[回答已中止]", "is_complete": True, "aborted": True}
        yield f"data: {json.dumps(abort_chunk)}\n\n"
        return
    
    if not files_to_analyze:
        error_chunk = {"chunk": "未找到要分析的文件。请上传文件后再尝试分析。", "is_complete": True}
        yield f"data: {json.dumps(error_chunk)}\n\n"
        return
    
    files_content = []
    for file_name in files_to_analyze:
        if file_name in conversation_files:
            file_info = conversation_files[file_name]
            files_content.append({
                "name": file_name,
                "type": file_info.get("type", "unknown"),
                "content": file_info.get("content", "")
            })
    
    if not files_content:
        error_chunk = {"chunk": "未找到要分析的文件。请上传文件后再尝试分析。", "is_complete": True}
        yield f"data: {json.dumps(error_chunk)}\n\n"
        return
    
    file_names = ", ".join([f["name"] for f in files_content])
    decision_chunk = {
        "chunk": f"根据您的请求，我将分析以下文件: {file_names}\n\n",
        "is_complete": False
    }
    yield f"data: {json.dumps(decision_chunk)}\n\n"
    
    # 检查中止状态
    if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
        app.aborted_streams.discard(request_id)
        abort_chunk = {"chunk": "\n\n[回答已中止]", "is_complete": True, "aborted": True}
        yield f"data: {json.dumps(abort_chunk)}\n\n"
        return
    
    combined_content = ""
    for idx, file_info in enumerate(files_content):
        combined_content += f"\n--- 文件 {idx+1}: {file_info['name']} (类型: {file_info['type']}) ---\n"
        combined_content += file_info["content"] + "\n\n"
    
    analysis_prompt = f"请分析以下文件内容并回答用户的问题:\n\n用户问题: {user_message}\n\n文件内容: {combined_content}\n\n"
    
    recent_history = ""
    user_history = get_user_conversation_history(username, conversation_id)
    if len(user_history) > 2:
        recent_msgs = user_history[:-1][-3:]
        for msg in recent_msgs:
            if msg["role"] in ["user", "assistant"]:
                role_text = "用户" if msg["role"] == "user" else "助手"
                recent_history += f"{role_text}: {msg['content']}\n\n"
    
    full_analysis = f"根据您的请求，我将分析以下文件: {file_names}\n\n"
    for chunk in generate_response_streaming(analysis_prompt, recent_history, request_id=request_id):
        # 检查中止状态
        if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
            app.aborted_streams.discard(request_id)
            abort_chunk = {"chunk": "\n\n[回答已中止]", "is_complete": True, "aborted": True}
            yield f"data: {json.dumps(abort_chunk)}\n\n"
            return
        full_analysis += chunk
        progress_chunk = {"chunk": chunk, "is_complete": False}
        yield f"data: {json.dumps(progress_chunk)}\n\n"
    
    user_history.append({"role": "assistant", "content": full_analysis})
    set_user_conversation_history(username, conversation_id, user_history)
    
    end_chunk = {"chunk": "", "is_complete": True}
    yield f"data: {json.dumps(end_chunk)}\n\n"

def handle_qa_mode(user_message, conversation_id, rag_config, files_to_analyze=None, request_id=None, username='anonymous'):
    """处理问答模式的流式响应"""
    logger.info(f"启动QnA流程，RAG配置：{rag_config}")
    
    # 检查中止状态
    if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
        app.aborted_streams.discard(request_id)
        abort_chunk = {"chunk": "\n\n[回答已中止]", "is_complete": True, "aborted": True}
        yield f"data: {json.dumps(abort_chunk)}\n\n"
        return
    
    # 使用用户和对话的组合键获取上传文件
    user_conversation_key = f"{username}_{conversation_id}"
    conversation_files = uploaded_files.get(user_conversation_key, {})
    
    # 如果有文件需要分析，则进入文件分析模式
    if files_to_analyze:
        for chunk in handle_file_analysis_mode(user_message, conversation_id, files_to_analyze, conversation_files, request_id, username):
            yield chunk
        return
    
    # 检查是否是文件分析请求
    if any(conversation_files.values()):
        file_keywords = ["分析", "查看", "解读", "介绍", "读取", "显示", "查询", "文件", "内容"]
        if any(kw in user_message for kw in file_keywords):
            # 自动选择最新文件
            classified = classify_uploaded_files(conversation_files)
            for file_type in ["sde", "sdevice", "sprocess", "unknown"]:
                if classified[file_type]:
                    newest_file = max(classified[file_type], key=lambda x: x.get("upload_time", 0))
                    for chunk in handle_file_analysis_mode(user_message, conversation_id, [newest_file["name"]], conversation_files, request_id, username):
                        yield chunk
                    return
    
    user_history = get_user_conversation_history(username, conversation_id)
    formatted_history = "\n".join([f"{msg['role']}: {msg['content']}" for msg in user_history])
    
    full_response = ""
    
    # 使用RAG增强的问答
    if rag_config["use_rag"] and is_rag_manager_available and rag_config.get("config_id") != 'none':
        logger.info(f"使用RAG增强的问答，配置：{rag_config}")
        rag_context = get_relevant_context(
            rag_config["embedding_word"], 
            config_id='default', 
            top_k=rag_config["top_k"], 
            embedding_word=rag_config["embedding_word"]
        )
        
        if "未找到相关上下文" not in rag_context and "出错" not in rag_context:
            for chunk in generate_response_streaming(user_message, formatted_history, rag_context, request_id):
                # 检查中止状态
                if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
                    app.aborted_streams.discard(request_id)
                    abort_chunk = {"chunk": "\n\n[回答已中止]", "is_complete": True, "aborted": True}
                    yield f"data: {json.dumps(abort_chunk)}\n\n"
                    return
                full_response += chunk
                response_chunk = {"chunk": chunk, "is_complete": False}
                yield f"data: {json.dumps(response_chunk)}\n\n"
        else:
            for chunk in generate_response_streaming(user_message, formatted_history, request_id=request_id):
                # 检查中止状态
                if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
                    app.aborted_streams.discard(request_id)
                    abort_chunk = {"chunk": "\n\n[回答已中止]", "is_complete": True, "aborted": True}
                    yield f"data: {json.dumps(abort_chunk)}\n\n"
                    return
                full_response += chunk
                response_chunk = {"chunk": chunk, "is_complete": False}
                yield f"data: {json.dumps(response_chunk)}\n\n"
    else:
        for chunk in generate_response_streaming(user_message, formatted_history, request_id=request_id):
            # 检查中止状态
            if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
                app.aborted_streams.discard(request_id)
                abort_chunk = {"chunk": "\n\n[回答已中止]", "is_complete": True, "aborted": True}
                yield f"data: {json.dumps(abort_chunk)}\n\n"
                return
            full_response += chunk
            response_chunk = {"chunk": chunk, "is_complete": False}
            yield f"data: {json.dumps(response_chunk)}\n\n"
    
    user_history.append({"role": "assistant", "content": full_response})
    set_user_conversation_history(username, conversation_id, user_history)
    
    end_chunk = {"chunk": "", "is_complete": True}
    yield f"data: {json.dumps(end_chunk)}\n\n"

def handle_generate_mode(user_message, conversation_id, code_type, rag_config, device_type, config_id=None, request_id=None, username='anonymous'):
    """处理代码生成模式的流式响应"""
    logger.info(f"启动代码生成流程，代码类型：{code_type}，器件类型：{device_type}")
    
    # 检查中止状态
    if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
        app.aborted_streams.discard(request_id)
        abort_chunk = {"chunk": "\n\n[回答已中止]", "is_complete": True, "aborted": True}
        yield f"data: {json.dumps(abort_chunk)}\n\n"
        return
    
    mode_info_chunk = {
        "chunk": "",
        "is_complete": False,
        "processing_mode": "generate",
        "config_id": config_id or 'default',
        "mode_info": True
    }
    yield f"data: {json.dumps(mode_info_chunk)}\n\n"
    
    type_info_chunk = {
        "chunk": f"正在为您生成{device_type} {code_type}{'结构' if code_type == 'sde' else '仿真'}代码...\n\n",
        "is_complete": False
    }
    yield f"data: {json.dumps(type_info_chunk)}\n\n"
    
    # 检查中止状态
    if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
        app.aborted_streams.discard(request_id)
        abort_chunk = {"chunk": "\n\n[回答已中止]", "is_complete": True, "aborted": True}
        yield f"data: {json.dumps(abort_chunk)}\n\n"
        return
    
    user_history = get_user_conversation_history(username, conversation_id)
    formatted_history = "\n".join([f"{msg['role']}: {msg['content']}" for msg in user_history])
    
    if code_type == "sde":
        # 初始化模板管理器
        template_manager = DeviceCodeTemplateManager()
        
        # 获取参考模板
        reference_template = template_manager.get_template(device_type)
        
        # 构建增强型提示词
        generation_prompt = f"""请参考以下{device_type.upper()}代码模板，根据用户需求生成一个完整的SDE代码：

=== 参考代码模板 ===
{reference_template}

=== 用户需求 ===
{user_message}

=== 代码生成指导 ===
请基于用户的具体需求，参考上述模板结构，生成一个完整的SDE代码：

1. **参数设置**：根据用户需求设置合适的几何参数、掺杂浓度等
2. **结构创建**：使用合适的几何创建函数构建器件结构
3. **网格优化**：根据器件尺寸和精度需求设置网格密度
4. **掺杂定义**：根据器件类型和性能要求设置掺杂分布
5. **接触定义**：确保接触位置和定义正确

请生成一个完整的、可直接运行的SDE代码，确保：
- 所有几何尺寸合理且符合物理规律
- 网格密度适当，关键区域足够精细
- 掺杂浓度和分布符合器件工艺要求
- 接触定义正确且位置合理
- 代码结构清晰，注释完整
- 模板中的引号请不要省略

请勿说明你是基于代码生成的脚本
请直接输出完整的SDE代码，用```sde和```包裹。
"""
        
        # 使用RAG增强（如果可用）
        if rag_config.get("use_rag", False) and is_rag_manager_available and config_id != 'none':
            rag_context = get_relevant_context(
                f"{device_type} sde structure", 
                config_id=config_id, 
                top_k=rag_config.get("top_k", 5)
            )
            if rag_context and "未找到" not in rag_context:
                generation_prompt += f"\n\n=== 相关技术资料 ===\n{rag_context}\n"
        
        # 流式生成优化代码
        full_response = f"已生成{device_type.upper()}基础结构，正在进行个性化优化：\n\n"
        for chunk in generate_response_streaming(generation_prompt, request_id=request_id):
            # 检查中止状态
            if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
                app.aborted_streams.discard(request_id)
                abort_chunk = {"chunk": "\n\n[回答已中止]", "is_complete": True, "aborted": True}
                yield f"data: {json.dumps(abort_chunk)}\n\n"
                return
            full_response += chunk
            progress_chunk = {"chunk": chunk, "is_complete": False}
            yield f"data: {json.dumps(progress_chunk)}\n\n"
        

    else:
        # SDEVICE代码生成逻辑
        generation_prompt = f"请根据用户的请求生成SDEVICE仿真设置代码。\n\n用户请求: {user_message}\n\n"
        
        if rag_config.get("use_rag", False) and config_id and config_id != 'none' and is_rag_manager_available:
            rag_context = get_relevant_context(
                rag_config.get("embedding_word", user_message), 
                config_id=config_id, 
                top_k=rag_config.get("top_k", 5)
            )
            if rag_context and "未找到相关上下文" not in rag_context:
                generation_prompt += f"相关知识库内容:\n{rag_context}\n\n"
        
        generation_prompt += f"对话历史:\n{formatted_history}\n\n"
        generation_prompt += "请生成一个完整的SDEVICE仿真代码，使用tcl语言。确保包含:\n- 物理模型设置\n- 合适的数值方法配置\n- 完整的仿真条件设置\n- 输出参数定义\n生成的代码应当以*.des.cmd为文件命名规范\n"
        generation_prompt += "请确保使用正确的Sentaurus TCAD语法，并用```sdevice和```包裹代码。"
        
        full_response = ""
        for chunk in generate_response_streaming(generation_prompt, request_id=request_id):
            # 检查中止状态
            if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
                app.aborted_streams.discard(request_id)
                abort_chunk = {"chunk": "\n\n[回答已中止]", "is_complete": True, "aborted": True}
                yield f"data: {json.dumps(abort_chunk)}\n\n"
                return
            full_response += chunk
            progress_chunk = {"chunk": chunk, "is_complete": False}
            yield f"data: {json.dumps(progress_chunk)}\n\n"
    
    user_history.append({"role": "assistant", "content": full_response})
    set_user_conversation_history(username, conversation_id, user_history)
    
    # 如果是SDE代码生成，尝试执行并生成可视化
    if code_type == "sde":
        for chunk in execute_sde_and_visualize(full_response, conversation_id, device_type, max_retries=2, current_retry=0, request_id=request_id):
            # 检查中止状态
            if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
                app.aborted_streams.discard(request_id)
                abort_chunk = {"chunk": "\n\n[回答已中止]", "is_complete": True, "aborted": True}
                yield f"data: {json.dumps(abort_chunk)}\n\n"
                return
            yield chunk
    
    end_chunk = {"chunk": "", "is_complete": True}
    yield f"data: {json.dumps(end_chunk)}\n\n"

# LLM API调用函数
def call_api_streaming(system_prompt, user_prompt, max_tokens, model=None, temperature=0.7):
    """调用SiliconFlow API并返回流式响应"""
    if model is None:
        model = llm_model
    
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": 0.5,
        "stream": True
    }
    
    try:
        response = session.post(API_URL, headers=headers, json=payload, stream=True, timeout=60)
        response.raise_for_status()
        return response
    except Exception as e:
        logger.error(f"API请求失败: {e}")
        
        class ErrorResponse:
            def iter_lines(self):
                error_json = {"choices": [{"delta": {"content": "文件内容过长，无法完全处理。已为您分析可处理的部分内容。"}}]}
                yield f"data: {json.dumps(error_json)}".encode('utf-8')
                yield "data: [DONE]".encode('utf-8')
        
        return ErrorResponse()

def process_streaming_response(response, yield_function=None, request_id=None):
    """处理流式响应"""
    if response is None:
        error_msg = "API请求失败，未收到有效响应"
        if yield_function:
            yield_function(error_msg)
        yield error_msg
        return
    
    handler = StreamingResponseHandler()
    
    try:
        for line in response.iter_lines():
            # 检查是否被中止
            if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
                logger.info(f"检测到请求 {request_id} 已被中止，停止流式输出")
                app.aborted_streams.discard(request_id)
                if yield_function:
                    yield_function("\n\n[回答已中止]")
                yield "\n\n[回答已中止]"
                return
                
            if line:
                line = line.decode('utf-8')
                
                if line.startswith("data: "):
                    line = line[6:]
                
                if line == "[DONE]":
                    break
                
                try:
                    json_data = json.loads(line)
                    
                    if "error" in json_data:
                        error_message = json_data.get("error", {}).get("message", "未知错误")
                        logger.error(f"API返回错误: {error_message}")
                        yield f"处理过程中出错: {error_message}"
                        continue
                    
                    delta = json_data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    
                    if delta:
                        processed_chunk = handler.on_chunk(delta)
                        
                        if yield_function:
                            yield_function(processed_chunk)
                        
                        yield processed_chunk
                except json.JSONDecodeError:
                    logger.warning(f"无法解析JSON: {line}")
                    continue
                except Exception as e:
                    logger.error(f"处理响应数据时出错: {str(e)}")
                    continue
        
        full_response, elapsed_time = handler.on_complete()
        logger.info(f"流式响应完成，耗时: {elapsed_time:.2f}秒，总长度: {len(full_response)}字符")
    except Exception as e:
        error_msg = f"处理流式响应时出错: {e}"
        logger.error(error_msg)
        yield error_msg

class StreamingResponseHandler:
    """处理流式响应的工具类"""
    def __init__(self):
        self.full_response = ""
        self.start_time = time.time()
    
    def on_chunk(self, chunk):
        self.full_response += chunk
        return chunk
    
    def on_complete(self):
        elapsed_time = time.time() - self.start_time
        return self.full_response, elapsed_time

def extract_complete_response(response):
    """从流式响应中提取完整的响应文本"""
    full_text = ""
    try:
        for chunk in process_streaming_response(response):
            full_text += chunk
        return full_text
    except Exception as e:
        logger.error(f"提取完整响应时出错: {e}")
        return f"提取响应出错: {str(e)}"

# 流式生成函数
def analyze_tcad_files_streaming(file_content, file_type, request_id=None):
    """流式分析TCAD文件内容"""
    prompt = (
        "你是FabGPT。请分析以下文件，并根据文件内容提供详细的分析。如果文件类型是：\n"
        "- SDE（器件结构定义），请输出器件结构分析，包括关键尺寸、材料、掺杂、网格设置、接触定义等；\n"
        "- SDEVICE（仿真文件定义），请输出仿真设置分析，包括仿真类型、物理模型、数值设置、求解策略、监测变量和边界条件等。\n\n"
        f"文件内容：\n{file_content}\n\n"
        "请按照如下格式输出，仅输出分析报告，不附加其他内容：\n"
        "===== 器件结构分析 =====\n"
        "...\n"
        "===== 仿真设置分析 =====\n"
        "..."
    )
    
    response = call_api_streaming(system_prompt, prompt, max_tokens=TokenConfig.LONG_RESPONSE)
    return process_streaming_response(response, request_id=request_id)

def generate_response_streaming(user_message, conversation_history=None, context=None, request_id=None):
    """生成流式回答"""
    if context:
        user_prompt = (
            f"基于以下对话历史和知识库信息，回答用户的最新问题：\n\n"
            f"对话历史：\n{conversation_history}\n\n"
            f"知识库相关信息：\n{context}\n\n"
            f"用户的最新问题是：{user_message}\n"
            f"请不要在回答中提及你是基于文档回答的。"
        )
    else:
        user_prompt = (
            f"基于以下对话历史，回答用户的最新问题：\n\n{conversation_history}\n\n"
            f"用户的最新问题是：{user_message}"
        )
    
    logger.info(f"Prompt长度: {len(user_prompt)}字符")
    response = call_api_streaming(system_prompt, user_prompt, max_tokens=TokenConfig.LONG_RESPONSE)
    return process_streaming_response(response, request_id=request_id)

def generate_code_changes_streaming(original_code, user_request, request_id=None):
    """流式生成代码修改建议"""
    user_prompt = (
        "你是FabGPT。在原代码的基础上根据用户需求通过#define定义用户需要的参数，"
        "参数名称左右两边用下划线包裹注明, 如'define _DF_ GradQuasiFermi'. 只需要生成#define开头的语句，"
        "除此之外不要关注其他需求，只返回代码的修改部分，不要任何引号包裹或说明，禁止删除或修改原代码中的任何内容。"
        "原代码为\n" + original_code + "\n用户的需求为\n" + user_request
    )
    
    response = call_api_streaming(None, user_prompt, max_tokens=TokenConfig.MEDIUM_RESPONSE)
    return process_streaming_response(response, request_id=request_id)

def analyze_simulation_results_streaming(sde_analysis, sim_analysis, output_result, processed_log, optimization_log, request_id=None):
    """流式生成仿真结果分析"""
    user_prompt = (
        "请结合以下信息，"
        "分析当前器件性能与仿真日志，并给出具体改进建议：\n\n"
        "器件结构分析：\n" + sde_analysis + "\n\n" +
        "仿真文件分析：\n" + sim_analysis + "\n\n" +
        "仿真输出指标：\n" + output_result + "\n\n" +
        "日志信息：\n" + processed_log + "\n\n" +
        "请给出改进建议和可能的参数修改策略，无需给出代码示例。"
    )
    
    response = call_api_streaming(system_prompt, user_prompt, max_tokens=TokenConfig.LONG_RESPONSE)
    return process_streaming_response(response, request_id=request_id)

# RAG相关函数
def check_rag_manager_availability():
    """检查RAG Manager负载均衡器服务可用性"""
    global is_rag_manager_available
    logger.info(f"检查RAG Manager负载均衡器是否可用...")
    
    try:
        response = requests.get(f"http://{RAG_MANAGER_HOST}:{RAG_MANAGER_PORT}/health", timeout=5)
        if response.status_code == 200:
            is_rag_manager_available = True
            logger.info(f"RAG Manager负载均衡器(端口{RAG_MANAGER_PORT})可用")
        else:
            is_rag_manager_available = False
            logger.warning(f"RAG Manager负载均衡器(端口{RAG_MANAGER_PORT})响应异常: {response.status_code}")
    except Exception as e:
        is_rag_manager_available = False
        logger.warning(f"RAG Manager负载均衡器(端口{RAG_MANAGER_PORT})不可用: {e}")
    
    logger.info(f"RAG Manager服务状态: {'可用' if is_rag_manager_available else '不可用'}")
    return is_rag_manager_available

def get_relevant_context(query, config_id='default', top_k=5, embedding_word=""):
    """从RAG Manager服务获取与查询相关的上下文"""
    try:
        logger.info(f"RAG查询: {query}, 使用知识库ID: {config_id}, Top-k: {top_k}, 嵌入词: {embedding_word}")
        
        # 使用负载均衡选择RAG Manager端口
        rag_port = get_available_rag_manager_port()
        
        url = f"http://{RAG_MANAGER_HOST}:{rag_port}/get_relevant_context"
        payload = {
            "query": embedding_word,
            "config_id": config_id,
            "service": "tcad",
            "query_timestamp": int(time.time()),
            "top_k": top_k,
        }
        
        logger.info(f"向RAG Manager发送请求: URL={url}, Payload={payload}")
        response = requests.post(url, json=payload, timeout=120)
        
        if response.status_code == 200:
            context_data = response.json()
            raw_context = context_data.get("context", "")
            knowledge_base_name = context_data.get("knowledge_base_name", "")
            search_id = context_data.get("search_id", "")

            if config_id == 'none' or "未找到相关上下文" in raw_context or "无知识库" in raw_context:
                logger.info(f"RagManager确认使用'无'知识库配置")
                return "知识库配置为'无'，无法检索相关内容。"
            
            if raw_context and "未找到" not in raw_context and "出错" not in raw_context:
                logger.info(f"成功获取RAG上下文，长度: {len(raw_context)} 字符，使用知识库: {knowledge_base_name}, 搜索ID: {search_id}")
                cleaned_context = (
                    "以下是与查询相关的半导体TCAD仿真的专业信息：\n\n"
                    f"{raw_context}\n"
                )
                return cleaned_context
            else:
                logger.warning(f"RAG查询未返回有效内容: {raw_context[:100]}...")
                return "知识库中未找到相关上下文。将直接回答您的问题。"
        else:
            error_msg = f"RAG查询失败: HTTP {response.status_code}"
            logger.error(error_msg)
            return "从知识库检索上下文时出错。将直接回答您的问题。"
            
    except Exception as e:
        logger.error(f"[RAG ERROR] get_relevant_context函数出错: {str(e)}")
        return f"访问知识库时出错。将直接回答您的问题。"

# 文件处理函数
def get_conversation_paths(conversation_id, username='anonymous'):
    """获取特定对话的文件路径 - 包含用户分离"""
    safe_conv_id = secure_filename(conversation_id)
    safe_username = secure_filename(username)
    # 使用用户名+对话ID组合确保用户分离
    user_conv_dir = f"{safe_username}_{safe_conv_id}"
    conv_dir = os.path.join(app.config['UPLOAD_FOLDER'], user_conv_dir)
    output_dir = os.path.join(conv_dir, "outputs")
    
    os.makedirs(conv_dir, exist_ok=True)
    os.makedirs(output_dir, exist_ok=True)
    
    return {
        "base_dir": conv_dir,
        "output_dir": output_dir,
        "user_conv_key": user_conv_dir,
        "output_file_path": os.path.join(conv_dir, f"outfile_des_{safe_conv_id}.cmd"),
        "analysis_output_file": os.path.join(conv_dir, f"tcad_analysis_{safe_conv_id}.txt"),
        "output_image_path": os.path.join(output_dir, f"outputs_node3_{safe_conv_id}.png"),
        "output_result_path": os.path.join(output_dir, f"outputs_result3_{safe_conv_id}.txt"),
        "output_log_path": os.path.join(output_dir, f"n3_des_{safe_conv_id}.out"),
    }

def get_generate_paths(conversation_id, username='anonymous'):
    """获取特定对话的代码生成文件路径 - 包含用户分离"""
    safe_conv_id = secure_filename(conversation_id)
    safe_username = secure_filename(username)
    # 使用用户名+对话ID组合确保用户分离
    user_conv_dir = f"{safe_username}_{safe_conv_id}"
    conv_generate_dir = os.path.join(app.config['GENERATE_FOLDER'], user_conv_dir)
    conv_upload_output_dir = os.path.join(app.config['UPLOAD_FOLDER'], user_conv_dir, "outputs")
    
    os.makedirs(conv_generate_dir, exist_ok=True)
    os.makedirs(conv_upload_output_dir, exist_ok=True)
    
    return {
        "base_dir": conv_generate_dir,
        "upload_output_dir": conv_upload_output_dir,
    }

def model_classify_file(filename):
    """基于文件名后缀判断文件类型"""
    filename_lower = filename.lower()
    
    if '_dvs.cmd' in filename_lower:
        return {"type": "sde", "reason": "基于文件名后缀判断为结构定义文件"}
    elif '_des.cmd' in filename_lower:
        return {"type": "sdevice", "reason": "基于文件名后缀判断为仿真设置文件"}
    elif '_pcs.cmd' in filename_lower:
        return {"type": "sprocess", "reason": "基于文件名后缀判断为工艺流程文件"}
    else:
        return {"type": "unknown", "reason": "无法通过文件名判断文件类型"}

def classify_uploaded_files(files_dict):
    """分类已上传的文件"""
    classified = {"sde": [], "sdevice": [], "sprocess": [], "unknown": []}
    
    for fname, finfo in files_dict.items():
        classification_result = model_classify_file(fname)
        file_type = classification_result.get("type", "unknown")
        finfo["type"] = file_type
        finfo["reason"] = classification_result.get("reason", "")
        if file_type in classified:
            classified[file_type].append(finfo)
        else:
            classified["unknown"].append(finfo)
    
    return classified

def read_file(file_path):
    """读取文件内容"""
    if not os.path.exists(file_path):
        logger.error(f"文件不存在: {file_path}")
        return ""
    
    file_size = os.path.getsize(file_path)
    if file_size > 50 * 1024 * 1024:
        logger.warning(f"文件过大，跳过完整读取: {file_path} ({file_size} 字节)")
        return f"[文件过大 ({file_size} 字节)，无法完全读取]"
    
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            return file.read()
    except UnicodeDecodeError:
        try:
            with open(file_path, 'r', encoding='gbk') as file:
                return file.read()
        except:
            try:
                with open(file_path, 'r', encoding='latin-1') as file:
                    return file.read()
            except Exception as e:
                logger.error(f"读取文件出错: {e}")
                return ""
    except Exception as e:
        logger.error(f"读取文件出错: {e}")
        return ""

def process_sentaurus_log(input_log_file, output_file):
    """处理Sentaurus日志文件以提取关键信息"""
    try:
        with open(input_log_file, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        try:
            with open(input_log_file, 'r', encoding='latin-1') as f:
                lines = f.readlines()
        except Exception as e:
            logger.error(f"读取日志文件时出错: {e}")
            return "无法读取日志文件"
    except FileNotFoundError:
        logger.error(f"Sentaurus日志文件未找到: {input_log_file}")
        return "日志文件未找到"
    
    extracted_info = ["SENTAURUS TCAD SIMULATION SUMMARY", "=" * 40 + "\n"]
    
    # 提取版本信息
    for i, line in enumerate(lines[:50]):
        if "Sentaurus Device" in line and "Version" in line:
            extracted_info.extend(["Simulation Environment:", "-" * 40, line.strip()])
            for j in range(i+1, min(i+5, len(lines))):
                if lines[j].strip() and not lines[j].startswith('*'):
                    extracted_info.append(lines[j].strip())
            extracted_info.append("")
    
    # 提取求解器信息
    solve_info = []
    in_solve_section = False
    for line in lines:
        if "Solve :" in line:
            solve_info.extend(["Solver Configuration:", "-" * 40])
            in_solve_section = True
        elif in_solve_section and line.strip():
            if "===============" in line:
                in_solve_section = False
            else:
                solve_info.append(line.strip())
    if solve_info:
        extracted_info.extend(solve_info)
        extracted_info.append("")
    
    processed_log = "\n".join(extracted_info)
    
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write("Processed Sentaurus Log Analysis\n" + "=" * 40 + "\n\n" + processed_log)
    except Exception as e:
        logger.error(f"保存处理后的日志时出错: {e}")
    
    return processed_log

def save_to_tcl_file(file_path, content):
    """保存内容到TCL文件"""
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return True
    except Exception as e:
        logger.error(f"保存文件时出错: {e}")
        return False

def extract_code_from_text(text):
    """从文本中提取代码块"""
    # 优先匹配带sde标识的代码块
    sde_pattern = r"```(?:sde|SDE)\s*([\s\S]*?)```"
    sde_matches = re.findall(sde_pattern, text, re.DOTALL)
    
    if sde_matches:
        return sde_matches[-1].strip()
    
    # 然后匹配带tcl标识的代码块
    tcl_pattern = r"```(?:tcl|Tcl|TCL)\s*([\s\S]*?)```"
    tcl_matches = re.findall(tcl_pattern, text, re.DOTALL)
    
    if tcl_matches:
        return tcl_matches[-1].strip()
    
    # 最后匹配通用代码块
    alt_pattern = r"```\s*([\s\S]*?)```"
    alt_matches = re.findall(alt_pattern, text, re.DOTALL)
    
    if alt_matches:
        code = alt_matches[-1].strip()
        # 🔥 新增：如果代码开头是语言标识符，则去掉它
        if code.startswith(('sde\n', 'SDE\n', 'tcl\n', 'TCL\n', 'Tcl\n')):
            lines = code.split('\n', 1)
            if len(lines) > 1:
                return lines[1].strip()
        return code
    
    return ""

# 路由端点
@app.route('/uploadFile', methods=['POST'])
def upload_file():
    """文件上传接口"""
    try:
        file = request.files['file']
        
        file.seek(0, os.SEEK_END)
        file_size = file.tell()
        file.seek(0)
        
        if file_size > 15 * 1024 * 1024:
            return jsonify({'error': '文件过大，请上传小于15MB的文件'}), 413
        
        conversation_id = request.form.get('conversation_id', 'default')
        config_id = request.form.get('config_id', 'default')
        # 获取用户名，用于分离文件和对话历史
        username = request.form.get('user_id', 'anonymous') or request.form.get('username', 'anonymous')
        
        # 为每个用户创建独立的文件目录
        user_conversation_dir = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(username), secure_filename(conversation_id))
        os.makedirs(user_conversation_dir, exist_ok=True)
        
        # 使用用户和对话的组合键作为uploaded_files的key
        user_conversation_key = f"{username}_{conversation_id}"
        if user_conversation_key not in uploaded_files:
            uploaded_files[user_conversation_key] = {}
        
        secure_name = secure_filename(file.filename)
        timestamp = int(time.time())
        file_base, file_ext = os.path.splitext(secure_name)
        timestamped_name = f"{file_base}_{timestamp}{file_ext}"
        
        file_path = os.path.join(user_conversation_dir, timestamped_name)
        file.save(file_path)
        
        file_content = read_file(file_path)
        if not file_content:
            return jsonify({'error': '无法读取文件内容'}), 500
        
        file_type_result = model_classify_file(file.filename)
        file_type = file_type_result.get("type", "unknown")
        
        file_info = {
            "name": file.filename,
            "saved_name": timestamped_name,
            "path": file_path,
            "content": file_content,
            "type": file_type,
            "reason": file_type_result.get("reason", ""),
            "original_size": len(file_content),
            "processed_size": len(file_content),
            "upload_time": timestamp
        }
        
        uploaded_files[user_conversation_key][file.filename] = file_info
        
        # 使用用户特定的对话历史
        user_history = get_user_conversation_history(username, conversation_id)
        
        file_upload_record = {
            "role": "system", 
            "content": f"文件 '{file.filename}' 上传成功",
            "metadata": {
                "action": "file_upload",
                "file_name": file.filename,
                "file_type": file_type,
                "timestamp": timestamp,
                "file_info_key": file.filename
            }
        }
        user_history.append(file_upload_record)
        set_user_conversation_history(username, conversation_id, user_history)
        
        logger.info(f"{file.filename} 已成功上传至 {file_path}，类型：{file_type}，对话ID：{conversation_id}")
        return jsonify({'message': f"文件 '{file.filename}' 解析成功，类型：{file_type}"}), 200
    except Exception as e:
        logger.error(f'文件上传错误: {e}')
        return jsonify({'error': '服务器内部错误'}), 500

@app.route('/stream_generate', methods=['POST'])
def stream_generate():
    def generate():
        try:
            data = json.loads(request.data)
            user_message = data.get('message', '')
            conversation_id = data.get('conversation_id', 'default')
            config_id = data.get('config_id', 'default')
            # 获取用户名
            username = data.get('user_id', 'anonymous')  # 从前端的user_id字段获取
            
            # 如果用户没有指定配置或指定为默认，则查询用户的活跃配置
            actual_config_id = config_id
            if config_id == 'default' and is_rag_manager_available:
                try:
                    # 向RAG Manager查询用户的活跃配置
                    rag_port = get_available_rag_manager_port()
                    response = requests.get(
                        f"http://{RAG_MANAGER_HOST}:{rag_port}/get_user_active_config?user_id={username}",
                        timeout=5
                    )
                    if response.status_code == 200:
                        active_data = response.json()
                        if active_data.get('success') and active_data.get('active_config'):
                            actual_config_id = active_data['active_config']
                            logger.info(f"TCAD用户 {username} 的活跃配置: {actual_config_id}")
                except Exception as e:
                    logger.warning(f"TCAD获取用户活跃配置失败: {str(e)}，使用传入的配置ID")
            
            # 生成请求ID
            request_id = f"{username}_{conversation_id}_{str(time.time())}"
            logger.info(f"收到流式请求: {user_message[:50]}...，用户ID：{username}，对话ID：{conversation_id}, 请求ID: {request_id}")
            
            # 初始化中止集合
            if not hasattr(app, 'aborted_streams'):
                app.aborted_streams = set()
            
            # 使用用户特定的对话历史
            user_history = get_user_conversation_history(username, conversation_id)
            user_history.append({"role": "user", "content": user_message})
            
            # 使用用户和对话的组合键获取上传文件
            user_conversation_key = f"{username}_{conversation_id}"
            conversation_files = uploaded_files.get(user_conversation_key, {})
            logger.info(f"用户上传文件上下文 - key: {user_conversation_key}, 文件数: {len(conversation_files)}")
            if conversation_files:
                logger.info(f"已找到文件: {list(conversation_files.keys())}")
            else:
                logger.warning(f"未找到用户 {username} 在对话 {conversation_id} 中的上传文件")
            classified = classify_uploaded_files(conversation_files)
            
            # 使用统一决策agent，传入实际的配置ID
            decision = unified_decision_agent(user_message, conversation_id, classified, actual_config_id, username)
            
            logger.info(f"统一决策结果: {decision}")
            
            # 检查决策结果
            if decision is None:
                error_chunk = {
                    "chunk": "抱歉，处理您的请求时遇到了问题。请稍后再试。",
                    "is_complete": True,
                    "error": True
                }
                yield f"data: {json.dumps(error_chunk)}\n\n"
                return
            
            # 发送开始信号
            start_chunk = {
                "chunk": "",
                "is_complete": False,
                "start_streaming": True,
                "request_id": request_id,
                "processing_mode": decision["mode"],
                "device_type": decision["device_type"],
                "config_id": config_id,
                "mode_info": True
            }
            yield f"data: {json.dumps(start_chunk)}\n\n"
            
            try:
                # 根据决策模式分发处理
                if decision["mode"] == "simulation":
                    logger.info("进入仿真模式处理")
                    for chunk in handle_simulation_mode(user_message, conversation_id, decision["files_used"], request_id, username):
                        # 检查中止状态
                        if request_id in app.aborted_streams:
                            logger.info(f"检测到请求 {request_id} 已被中止，停止流式输出")
                            app.aborted_streams.discard(request_id)
                            abort_chunk = {"chunk": "\n\n[回答已中止]", "is_complete": True, "aborted": True}
                            yield f"data: {json.dumps(abort_chunk)}\n\n"
                            return
                        yield chunk
                elif decision["mode"] == "generate":
                    logger.info(f"进入代码生成模式处理，代码类型：{decision['code_type']}，器件类型：{decision['device_type']}")
                    for chunk in handle_generate_mode(
                        user_message, 
                        conversation_id, 
                        decision["code_type"], 
                        decision["rag_config"], 
                        decision["device_type"],
                        config_id=config_id,
                        request_id=request_id,
                        username=username  # 添加username参数
                    ):
                        # 检查中止状态
                        if request_id in app.aborted_streams:
                            logger.info(f"检测到请求 {request_id} 已被中止，停止流式输出")
                            app.aborted_streams.discard(request_id)
                            abort_chunk = {"chunk": "\n\n[回答已中止]", "is_complete": True, "aborted": True}
                            yield f"data: {json.dumps(abort_chunk)}\n\n"
                            return
                        yield chunk
                else:
                    logger.info(f"进入问答模式处理，RAG配置：{decision['rag_config']}")
                    files_to_analyze = decision["files_used"].get("files_to_analyze", None)
                    for chunk in handle_qa_mode(
                        user_message, 
                        conversation_id, 
                        decision["rag_config"], 
                        files_to_analyze=files_to_analyze, 
                        request_id=request_id,
                        username=username  # 添加username参数
                    ):
                        # 检查中止状态
                        if request_id in app.aborted_streams:
                            logger.info(f"检测到请求 {request_id} 已被中止，停止流式输出")
                            app.aborted_streams.discard(request_id)
                            abort_chunk = {"chunk": "\n\n[回答已中止]", "is_complete": True, "aborted": True}
                            yield f"data: {json.dumps(abort_chunk)}\n\n"
                            return
                        yield chunk
                
                # 清理中止状态
                app.aborted_streams.discard(request_id)
                        
            except GeneratorExit:
                logger.info(f"客户端断开连接，请求ID: {request_id}")
                app.aborted_streams.discard(request_id)
                
        except Exception as e:
            error_message = f"处理消息时出错: {str(e)}"
            logger.error(error_message)
            error_chunk = {"chunk": error_message, "is_complete": True}
            yield f"data: {json.dumps(error_chunk)}\n\n"
    
    return Response(stream_with_context(generate()), content_type='text/event-stream')

@app.route('/static/outputs/<path:filename>')
def serve_image(filename):
    """提供静态图片文件访问"""
    try:
        parts = filename.split('/')
        if len(parts) >= 2:
            # 支持用户分离的路径格式: username_conversationid/imagefile.png
            user_conversation_id = parts[0]  # 格式: username_conversationid
            image_filename = parts[1]
            
            # 直接使用包含用户信息的目录名
            conversation_dir = os.path.join(app.config['UPLOAD_FOLDER'], secure_filename(user_conversation_id))
            output_dir = os.path.join(conversation_dir, "outputs")
            
            image_path = os.path.join(output_dir, image_filename)
            if os.path.exists(image_path):
                logger.info(f"提供图片: {image_path}")
                response = send_from_directory(output_dir, image_filename)
                response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
                response.headers['Pragma'] = 'no-cache'
                response.headers['Expires'] = '0'
                response.headers['Last-Modified'] = str(os.path.getmtime(image_path))
                return response
            else:
                logger.error(f"图片不存在: {image_path}")
                return jsonify({'error': '图片不存在'}), 404
        else:
            logger.error(f"无效的文件路径格式: {filename}")
            return jsonify({'error': '无效的文件路径格式'}), 400
    except Exception as e:
        logger.error(f"提供图片时出错: {e}")
        return jsonify({'error': '提供图片时遇到服务器错误'}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """健康检查接口"""
    return jsonify({
        "status": "healthy",
        "version": "1.0.0",
        "rag_manager_available": is_rag_manager_available,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
    }), 200

@app.route('/abort_stream', methods=['POST'])
def abort_stream():
    """处理中止流式输出的请求"""
    try:
        data = request.json
        request_id = data.get('request_id')
        
        if not request_id:
            return jsonify({'error': '缺少请求ID'}), 400
        
        # 初始化中止集合
        if not hasattr(app, 'aborted_streams'):
            app.aborted_streams = set()
        
        # 标记该流式请求为已中止
        app.aborted_streams.add(request_id)
        
        return jsonify({
            'message': f"已标记请求 {request_id} 为中止状态",
            'success': True
        }), 200
    except Exception as e:
        logger.error(f'中止流式输出时出错: {e}')
        return jsonify({
            'error': '中止失败',
            'success': False
        }), 500

@app.route('/deleteFile', methods=['POST'])
def delete_file():
    """删除上传的文件"""
    try:
        data = request.json
        conversation_id = data.get('conversation_id', 'default')
        file_name = data.get('file_name')
        username = data.get('user_id', 'anonymous')
        
        if not file_name:
            return jsonify({'error': '未提供文件名'}), 400
        
        user_conversation_key = f"{username}_{conversation_id}"
        if user_conversation_key not in uploaded_files or file_name not in uploaded_files[user_conversation_key]:
            logger.warning(f"文件不存在，但仍返回成功状态: {file_name}")
            return jsonify({'message': f"文件 '{file_name}' 已成功删除", 'isDeleted': True}), 200
        
        file_info = uploaded_files[user_conversation_key][file_name]
        file_path = file_info.get('path')
        
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"物理文件已删除: {file_path}")
            except Exception as e:
                logger.error(f"删除物理文件时出错: {str(e)}")
        
        del uploaded_files[user_conversation_key][file_name]
        
        logger.info(f"文件 '{file_name}' 已从对话 {conversation_id} 中删除")
        return jsonify({'message': f"文件 '{file_name}' 已成功删除", 'isDeleted': True}), 200
    except Exception as e:
        logger.error(f'删除文件错误: {e}')
        return jsonify({'message': f"文件已标记为删除", 'isDeleted': True}), 200

@app.route('/clear_file_context', methods=['POST'])
def clear_file_context():
    """清理特定对话中的文件上下文"""
    try:
        data = request.json
        conversation_id = data.get('conversation_id')
        file_name = data.get('file_name')
        username = data.get('user_id', 'anonymous')
        
        if not conversation_id or not file_name:
            return jsonify({'error': '缺少必要参数'}), 400
        
        user_conversation_key = f"{username}_{conversation_id}"
        if user_conversation_key in uploaded_files and file_name in uploaded_files[user_conversation_key]:
            del uploaded_files[user_conversation_key][file_name]
            logger.info(f"已从会话 {conversation_id} 中清理文件 {file_name} 的上下文")
        
        # 使用用户特定的对话历史
        user_history = get_user_conversation_history(username, conversation_id)
        if user_history:
            updated_history = []
            for msg in user_history:
                if msg.get("role") == "system" and msg.get("metadata", {}).get("file_name") == file_name:
                    continue
                if msg.get("role") == "assistant" and file_name in msg.get("content", ""):
                    continue
                updated_history.append(msg)
            set_user_conversation_history(username, conversation_id, updated_history)
            logger.info(f"已清理对话历史中的文件 {file_name} 相关记录")
        
        return jsonify({'message': f"文件 {file_name} 的上下文已清理", 'isDeleted': True}), 200
    except Exception as e:
        logger.error(f'清理文件上下文时出错: {e}')
        return jsonify({'error': '清理失败', 'isDeleted': False}), 500

@app.route('/get_rag_configurations', methods=['GET'])
def get_rag_configurations():
    """获取所有知识库配置"""
    try:
        response = requests.get(f"http://{RAG_MANAGER_HOST}:{RAG_MANAGER_PORT}/get_rag_configurations", timeout=SOCKET_TIMEOUT)
        return Response(response.content, status=response.status_code, content_type=response.headers.get('content-type', 'application/json'))
    except Exception as e:
        logger.error(f'获取知识库配置错误: {str(e)}')
        return jsonify({'error': 'RAG Manager服务不可用'}), 503

@app.route('/set_active_configuration', methods=['POST'])
def set_active_configuration():
    """设置活跃的知识库配置"""
    try:
        data = request.json
        config_id = data.get('config_id')
        is_sync_request = data.get('is_sync_request', False)
        
        if not config_id:
            return jsonify({'error': '未提供配置ID'}), 400
        
        if is_sync_request:
            logger.info(f"收到同步请求: 设置知识库配置 {config_id}")
            return jsonify({'message': f"已通过同步请求设置知识库配置: {config_id}", 'success': True}), 200
        
        if is_rag_manager_available:
            # 使用负载均衡选择RAG Manager端口
            rag_port = get_available_rag_manager_port()
            
            response = requests.post(f"http://{RAG_MANAGER_HOST}:{rag_port}/set_active_configuration", json=data, timeout=SOCKET_TIMEOUT)
            return Response(response.content, status=response.status_code, content_type=response.headers.get('content-type', 'application/json'))
        else:
            return jsonify({'error': 'RagManager服务不可用'}), 503
    except Exception as e:
        logger.error(f'设置知识库配置错误: {str(e)}')
        return jsonify({'error': '服务器内部错误'}), 500

def execute_sde_and_visualize(generated_response, conversation_id, device_type, max_retries=2, current_retry=0, request_id=None):
    """执行SDE代码并生成可视化"""
    try:
        # 检查中止状态
        if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
            app.aborted_streams.discard(request_id)
            abort_msg = "\n\n[代码执行已中止]"
            yield f"data: {json.dumps({'chunk': abort_msg, 'is_complete': True, 'aborted': True})}\n\n"
            return
        
        # 检查重试次数
        if current_retry >= max_retries:
            max_retry_msg = f"⚠️ 已达到最大重试次数 ({max_retries})，停止尝试。请检查代码或手动修正。"
            yield f"data: {json.dumps({'chunk': max_retry_msg, 'is_complete': False})}\n\n"
            return
        
        # 如果是重试，显示重试信息
        if current_retry > 0:
            retry_info_msg = f"🔄 第 {current_retry + 1} 次尝试执行SDE代码..."
            yield f"data: {json.dumps({'chunk': retry_info_msg, 'is_complete': False})}\n\n"
        
        # 提取代码块
        sde_code = extract_code_from_text(generated_response)
        if not sde_code:
            error_msg = "\n\n未找到可执行的SDE代码块。"
            yield f"data: {json.dumps({'chunk': error_msg, 'is_complete': False})}\n\n"
            return
        
        processing_msg = "\n\n正在执行SDE代码生成网格..."
        yield f"data: {json.dumps({'chunk': processing_msg, 'is_complete': False})}\n\n"
        
        # 检查中止状态
        if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
            app.aborted_streams.discard(request_id)
            abort_msg = "\n\n[代码执行已中止]"
            yield f"data: {json.dumps({'chunk': abort_msg, 'is_complete': True, 'aborted': True})}\n\n"
            return
        
        # 🔥 使用新的generate_paths
        generate_paths = get_generate_paths(conversation_id)
        
        # 🔥 改进：添加时间戳和随机后缀避免冲突
        import random
        timestamp = int(time.time())
        random_suffix = random.randint(1000, 9999)
        unique_id = f"{timestamp}_{random_suffix}"
        
        # 🔥 在generate_files中创建专门的执行目录
        execution_dir = os.path.join(generate_paths["base_dir"], f"sde_execution_{unique_id}")
        os.makedirs(execution_dir, exist_ok=True)
        
        sde_file_path = os.path.join(execution_dir, f"generated_sde_{unique_id}.cmd")
        
        # 提取mesh名称（从build-mesh命令中）
        # 优先匹配带引号的格式
        mesh_name_match = re.search(r'sde:build-mesh\s+"([^"]+)"', sde_code)
        if not mesh_name_match:
            # 匹配不带引号的格式
            mesh_name_match = re.search(r'sde:build-mesh\s+([^\s\)]+)', sde_code)

        # 🔥 改进：确保mesh文件名唯一
        base_mesh_name = mesh_name_match.group(1) if mesh_name_match else device_type
        unique_mesh_name = f"{base_mesh_name}_{unique_id}"

        # 修改SDE代码中的mesh名称为唯一名称
        # 先处理有引号的情况
        if re.search(r'sde:build-mesh\s+"[^"]+"', sde_code):
            modified_sde_code = re.sub(
                r'(sde:build-mesh\s+)"([^"]+)"',
                rf'\1"{unique_mesh_name}"',
                sde_code
            )
        else:
            # 处理无引号的情况
            modified_sde_code = re.sub(
                r'(sde:build-mesh\s+)([^\s\)]+)',
                rf'\1{unique_mesh_name}',
                sde_code
            )

        # 保存修改后的SDE代码
        with open(sde_file_path, 'w', encoding='utf-8') as f:
            f.write(modified_sde_code)

        tdr_file_path = os.path.join(execution_dir, f"{unique_mesh_name}_msh.tdr")
        
        # 执行SDE命令
        try:
            result = subprocess.run(
                ["sde", "-e", "-l", sde_file_path],
                cwd=execution_dir,  # 🔥 在独立目录中执行
                check=True, 
                text=True, 
                capture_output=True,
                timeout=300
            )
            
            # 检查中止状态
            if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
                app.aborted_streams.discard(request_id)
                abort_msg = "\n\n[代码执行已中止]"
                yield f"data: {json.dumps({'chunk': abort_msg, 'is_complete': True, 'aborted': True})}\n\n"
                return
            
            # 检查是否生成了tdr文件
            if os.path.exists(tdr_file_path):
                success_msg = "✅ 网格生成成功！正在创建可视化..."
                yield f"data: {json.dumps({'chunk': success_msg, 'is_complete': False})}\n\n"
                
                # 生成可视化
                for chunk in generate_sde_visualization(tdr_file_path, unique_mesh_name, conversation_id, unique_id, request_id):
                    # 检查中止状态
                    if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
                        app.aborted_streams.discard(request_id)
                        abort_msg = "\n\n[可视化已中止]"
                        yield f"data: {json.dumps({'chunk': abort_msg, 'is_complete': True, 'aborted': True})}\n\n"
                        return
                    yield chunk
            else:
                warning_msg = "⚠️ SDE执行完成，但未找到预期的网格文件。"
                yield f"data: {json.dumps({'chunk': warning_msg, 'is_complete': False})}\n\n"
                
        except subprocess.CalledProcessError as e:
            # 检查SDE标准错误文件
            error_file = f"{sde_file_path}.log.err"
            error_message = ""
            
            if os.path.exists(error_file):
                try:
                    with open(error_file, 'r', encoding='utf-8') as f:
                        error_message = f.read().strip()
                        logger.info(f"成功读取错误文件: {error_file}")
                except Exception as read_err:
                    logger.warning(f"读取错误文件 {error_file} 失败: {read_err}")
            
            # 如果错误文件不存在或为空，使用subprocess的stderr
            if not error_message and e.stderr:
                error_message = e.stderr.strip()
            
            # 如果还是没有错误信息，使用通用错误信息
            if not error_message:
                error_message = f"SDE执行失败，退出码: {e.returncode}"
            
            error_msg = f"❌ SDE执行出错。正在分析错误并重新生成代码...\n\n错误信息：\n{error_message}"
            yield f"data: {json.dumps({'chunk': error_msg, 'is_complete': False})}\n\n"
            
            # 重新生成代码
            for chunk in regenerate_sde_code(generated_response, error_message, conversation_id, device_type, max_retries, current_retry, request_id):
                # 检查中止状态
                if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
                    app.aborted_streams.discard(request_id)
                    abort_msg = "\n\n[代码生成已中止]"
                    yield f"data: {json.dumps({'chunk': abort_msg, 'is_complete': True, 'aborted': True})}\n\n"
                    return
                yield chunk
                
    except Exception as e:
        logger.error(f"执行SDE代码时出错: {str(e)}")
        error_msg = f"执行SDE代码时发生错误: {str(e)}"
        yield f"data: {json.dumps({'chunk': error_msg, 'is_complete': False})}\n\n"

def generate_sde_visualization(tdr_file_path, mesh_name, conversation_id, unique_id, request_id=None):
    """生成SDE网格可视化"""
    try:
        # 检查中止状态
        if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
            app.aborted_streams.discard(request_id)
            abort_msg = "\n\n[可视化已中止]"
            yield f"data: {json.dumps({'chunk': abort_msg, 'is_complete': True, 'aborted': True})}\n\n"
            return
        
        generate_paths = get_generate_paths(conversation_id)
        execution_dir = os.path.dirname(tdr_file_path)  # 使用tdr文件所在目录
        
        tcl_script_path = os.path.join(execution_dir, f"visualize_{unique_id}.tcl")
        # 🔥 图片输出到upload_files的outputs目录中，保持与前端路由一致
        image_output_path = os.path.join(generate_paths["upload_output_dir"], f"sde_mesh_{unique_id}.png")
        
        # 创建TCL脚本
        tcl_content = f'''load_file "{tdr_file_path}"
create_plot -dataset {mesh_name}_msh
select_plots {{Plot_{mesh_name}_msh}}
set_camera_prop -plot Plot_{mesh_name}_msh -setup {{"0.015 0 -0.109" "1.86436 1.84936 1.401" "-0.353553 -0.353553 0.866025" 5.7997 1.95187}}
export_view "{image_output_path}" -plots {{Plot_{mesh_name}_msh}} -format png
exit
'''
        
        with open(tcl_script_path, 'w', encoding='utf-8') as f:
            f.write(tcl_content)
        
        # 检查中止状态
        if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
            app.aborted_streams.discard(request_id)
            abort_msg = "\n\n[可视化已中止]"
            yield f"data: {json.dumps({'chunk': abort_msg, 'is_complete': True, 'aborted': True})}\n\n"
            return
        
        # 执行svisual
        result = subprocess.run(
            ["svisual", "-bx", tcl_script_path],
            cwd=execution_dir,
            check=True,
            text=True,
            capture_output=True,
            timeout=60
        )
        
        if os.path.exists(image_output_path):
            # 生成图像HTML
            backend_url = "http://10.98.64.22:5002"
            image_url = f"{backend_url}/static/outputs/{conversation_id}/sde_mesh_{unique_id}.png"
            image_html = f"\n\n📊 **网格生成结果可视化**\n\n<img src=\"{image_url}\" alt=\"SDE Mesh Visualization\" width=\"600\">"
            
            yield f"data: {json.dumps({'chunk': image_html, 'is_complete': False})}\n\n"
        else:
            warning_msg = "⚠️ 可视化图像生成失败。"
            yield f"data: {json.dumps({'chunk': warning_msg, 'is_complete': False})}\n\n"
            
    except Exception as e:
        logger.error(f"生成SDE可视化时出错: {str(e)}")
        error_msg = f"生成可视化时发生错误: {str(e)}"
        yield f"data: {json.dumps({'chunk': error_msg, 'is_complete': False})}\n\n"

def regenerate_sde_code(original_response, error_message, conversation_id, device_type, max_retries=2, current_retry=0, request_id=None):
    """基于错误信息重新生成SDE代码"""
    try:
        # 检查中止状态
        if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
            app.aborted_streams.discard(request_id)
            abort_msg = "\n\n[代码重新生成已中止]"
            yield f"data: {json.dumps({'chunk': abort_msg, 'is_complete': True, 'aborted': True})}\n\n"
            return
        
        # 检查是否还能重试
        if current_retry >= max_retries:
            max_retry_msg = f"⚠️ 已达到最大重试次数 ({max_retries})，无法继续修正代码。\n\n请手动检查以下错误信息并修正代码：\n{error_message}"
            yield f"data: {json.dumps({'chunk': max_retry_msg, 'is_complete': False})}\n\n"
            return
        
        # 获取模板作为参考
        template_manager = DeviceCodeTemplateManager()
        reference_template = template_manager.get_template(device_type)
        
        regeneration_prompt = f'''您生成的SDE代码在执行时发生了错误（第 {current_retry + 1} 次尝试）：

错误信息：
{error_message}

原始代码：
{original_response}

参考模板（已验证正确）：
{reference_template}

请基于错误信息和参考模板，修正代码中的问题并重新生成一个完整的SDE代码。请特别注意：
1. 语法错误和拼写错误
2. 几何定义的合理性
3. 参数数值的有效性
4. 函数调用的正确性
5. 避免重复之前的错误

请勿说明你是基于模板生成的代码
请直接输出修正后的完整SDE代码，用```sde和```包裹。
'''
        
        fixing_msg = f"\n\n🔧 **代码修正中... (第 {current_retry + 1}/{max_retries} 次尝试)**\n\n"
        yield f"data: {json.dumps({'chunk': fixing_msg, 'is_complete': False})}\n\n"
        
        full_regenerated = ""
        for chunk in generate_response_streaming(regeneration_prompt, request_id=request_id):
            # 检查中止状态
            if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
                app.aborted_streams.discard(request_id)
                abort_msg = "\n\n[代码重新生成已中止]"
                yield f"data: {json.dumps({'chunk': abort_msg, 'is_complete': True, 'aborted': True})}\n\n"
                return
            full_regenerated += chunk
            yield f"data: {json.dumps({'chunk': chunk, 'is_complete': False})}\n\n"
        
        # 🔥 修改：递归执行修正后的代码，传递递增的重试次数
        if "```sde" in full_regenerated or "```" in full_regenerated:
            retry_msg = f"\n\n🔄 **执行修正后的代码... (第 {current_retry + 1}/{max_retries} 次尝试)**\n\n"
            yield f"data: {json.dumps({'chunk': retry_msg, 'is_complete': False})}\n\n"
            
            # 递增重试次数
            for chunk in execute_sde_and_visualize(full_regenerated, conversation_id, device_type, max_retries, current_retry + 1, request_id):
                # 检查中止状态
                if request_id and hasattr(app, 'aborted_streams') and request_id in app.aborted_streams:
                    app.aborted_streams.discard(request_id)
                    abort_msg = "\n\n[代码执行已中止]"
                    yield f"data: {json.dumps({'chunk': abort_msg, 'is_complete': True, 'aborted': True})}\n\n"
                    return
                yield chunk
        else:
            no_code_msg = "⚠️ 修正后的响应中未找到有效的代码块。"
            yield f"data: {json.dumps({'chunk': no_code_msg, 'is_complete': False})}\n\n"
                
    except Exception as e:
        logger.error(f"重新生成SDE代码时出错: {str(e)}")
        error_msg = f"重新生成代码时发生错误: {str(e)}"
        yield f"data: {json.dumps({'chunk': error_msg, 'is_complete': False})}\n\n"

# 清理函数
def cleanup_resources():
    """在应用关闭时清理资源"""
    logger.info("正在清理TCAD服务资源...")
    logger.info("所有资源已清理")

def signal_handler(sig, frame):
    """信号处理函数"""
    logger.info(f"接收到信号 {sig}，正在清理资源...")
    cleanup_resources()
    sys.exit(0)

# 主函数
if __name__ == '__main__':
    is_rag_manager_available = check_rag_manager_availability()
    
    if is_rag_manager_available:
        try:
            # 使用负载均衡选择RAG Manager端口
            rag_port = get_available_rag_manager_port()
            
            response = requests.get(f"http://{RAG_MANAGER_HOST}:{rag_port}/get_rag_configurations")
            if response.status_code == 200:
                data = response.json()
                rag_configurations = {config["id"]: config for config in data.get("configurations", [])}
                logger.info(f"已从RagManager同步 {len(rag_configurations)} 个知识库配置")
        except Exception as e:
            logger.error(f"从RagManager同步配置时出错: {str(e)}")
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    atexit.register(cleanup_resources)
    
    logger.info("=" * 50)
    logger.info(f"FabGPT TCAD服务实例{INSTANCE_ID}已启动 (优化版本)")
    logger.info(f"运行于 http://10.98.64.22:{SERVICE_PORT}")
    logger.info("并行处理增强，支持多用户并发")
    logger.info("=" * 50)
    
    print("✅ TCAD 服务启动完成")
    print(f"📍 服务地址: http://10.98.64.22:{SERVICE_PORT}")
    print("📋 主要端点: /stream_generate")
    print(f"🔗 状态检查: http://10.98.64.22:{SERVICE_PORT}/health")
    
    app.run(debug=False, host='0.0.0.0', port=SERVICE_PORT, threaded=True)
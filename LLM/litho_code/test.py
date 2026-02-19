def markdown():    
    data = [
            {"type": "text", "content": "这是第一段文字内容。"},
            {"type": "image", "url": "https://example.com/image1.png", "width": 300, "height": 200},
            {"type": "text", "content": "这是第二段文字内容，描述上一张图片。"}
        ]

        # 拼接 Markdown 内容
    markdown_content = ""
    for item in data:
        if item["type"] == "text":
            markdown_content += f"{item['content']}\n\n"
        elif item["type"] == "image":
            markdown_content += (
                f'<img src="{item["url"]}" alt="image" width="{item["width"]}" height="{item["height"]}">\n\n'
            )
            
    print(markdown_content)

def instruction(args):

    # 将参数处理为正常的命令行字符串
    normalized_command = ' '.join(f'"{arg}"' if ' ' in arg else arg for arg in args)

    print(normalized_command)
    
    
if __name__ == "__main__":
    args=['evaluate', '--mask', "/data/Web-FabGPT/LLM/litho_code/output_image/M1_test1_mask.png", '--target', '/data/Web-FabGPT/LLM/litho_code/thirdparty/OpenILT/benchmark/ICCAD2013/M1_test1.glp']
    # test_args=["optimize","--target","M1_test1.glp","--model","simpleilt",  "--tile_sizeX","2048","--tile_sizeY","2048","--output_format","printedNom"]
    # args=["simulate","--mask","/data/Web-FabGPT/LLM/litho_code/output_image/M1_test1_mask.png", "--tile_sizeX","2048","--tile_sizeY","2048","--output_format","printedNom"]
    instruction(args)
    # markdown()    
    # test()
    # test_online()
    # app.run(host='
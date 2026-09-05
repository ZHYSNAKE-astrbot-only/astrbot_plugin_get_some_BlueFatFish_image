from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import os
import random

@register("来点蓝色大肥鱼", "ZHYSNAKE", "一个简单的 随机发送图片 插件", "1.0.0")
class MyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)


    @filter.command("来点蓝色大肥鱼")
    async def get_some_image(self, event: AstrMessageEvent):
        """这是一个 来点蓝色大肥鱼 指令，发送之后会从图库里随机选一张发出来"""
        image_path = self.get_random_image("/image")
        if image_path:
            yield event.image_result(image_path) 
        else:
            yield event.plain_result("哎呀，图片文件夹是空的！")

    def get_random_image(image_folder_path):
        """ 从指定文件夹中随机选择一张图片 """
        # 支持的图片格式
        supported_formats = ('.png', '.jpg', '.jpeg', '.gif', '.webp')
        
        # 列出文件夹下所有文件（不含子目录）
        all_files = os.listdir(image_folder_path)
        
        # 过滤出图片文件
        images = [
            os.path.join(image_folder_path, f)  # 要放入列表的内容（完整路径）
            for f in all_files                  # 从哪里取
            if os.path.isfile(os.path.join(image_folder_path, f))  # 确保是文件
            and f.lower().endswith(supported_formats)              # 确保是图片
        ]
        
        return random.choice(images) if images else None
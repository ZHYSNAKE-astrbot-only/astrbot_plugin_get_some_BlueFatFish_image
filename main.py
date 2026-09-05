from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api import logger
from astrbot.api.star import Context, Star, register
import os
import random
import requests
import zipfile
import shutil
import threading

@register("来点蓝色大肥鱼", "ZHYSNAKE", "一个简单的 随机发送图片 插件", "1.0.0")
class MyPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        plugin_dir = os.path.dirname(os.path.abspath(__file__))  # 插件根目录
        self.image_folder = os.path.join(plugin_dir, "image")
        # 确保文件夹存在
        os.makedirs(self.image_folder, exist_ok=True)
        # 检查是否有图片，如果没有则后台下载
        if not self._has_images():
            logger.info("图片文件夹为空，开始后台下载...")
            threading.Thread(target=self.download_and_extract_images, daemon=True).start()

    @filter.command("来点蓝色大肥鱼")
    async def get_some_image(self, event: AstrMessageEvent):
        """这是一个 来点蓝色大肥鱼 指令，发送之后会从图库里随机选一张发出来"""
        image_path = self.get_random_image()
        if image_path:
            yield event.image_result(image_path) 
        else:
            yield event.plain_result("哎呀，图片文件夹是空的！")

    def get_random_image(self):
        """ 从指定文件夹中随机选择一张图片 """
        # 支持的图片格式
        supported_formats = ('.png', '.jpg', '.jpeg', '.gif', '.webp')
        
        # 列出文件夹下所有文件（不含子目录）
        all_files = os.listdir(self.image_folder)
        
        # 过滤出图片文件
        images = [
            os.path.join(self.image_folder, f)  # 要放入列表的内容（完整路径）
            for f in all_files                  # 从哪里取
            if os.path.isfile(os.path.join(self.image_folder, f))  # 确保是文件
            and f.lower().endswith(supported_formats)              # 确保是图片
        ]
        
        return random.choice(images) if images else None

    def _has_images(self):
        """检查 images 文件夹中是否已有图片"""
        if not os.path.exists(self.image_folder):
            return False
        for f in os.listdir(self.image_folder):
            if f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
                return True
        return False


    def download_and_extract_images(self): 
        """后台下载并解压图片"""
        try:
            zip_path = os.path.join(self.image_folder, "repo.zip")
            url = "https://gitee.com/ZHYSNAKE/plugin_only/zipball/main"
            logger.info("开始下载图片压缩包...")
            resp = requests.get(url, stream=True, allow_redirects=True, timeout=60)
            if resp.status_code != 200:
                logger.error(f"下载失败，状态码: {resp.status_code}")
                return

            with open(zip_path, 'wb') as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)

            logger.info("下载完成，开始解压...")
            extract_dir = os.path.join(self.image_folder, "_temp_extract")
            os.makedirs(extract_dir, exist_ok=True)
            with zipfile.ZipFile(zip_path, 'r') as z:
                z.extractall(extract_dir)

            # 移动所有图片到 image 根目录
            supported = ('.png', '.jpg', '.jpeg', '.gif', '.webp')
            for root, _, files in os.walk(extract_dir):
                for file in files:
                    if file.lower().endswith(supported):
                        src = os.path.join(root, file)
                        dst = os.path.join(self.image_folder, file)
                        # 如果重名则覆盖
                        shutil.move(src, dst)

            # 清理临时文件
            shutil.rmtree(extract_dir, ignore_errors=True)
            os.remove(zip_path)
            logger.info("解压完成")
        except Exception as e:
            logger.error(f"下载或解压过程中出错: {e}")
            # 清理残留
            if os.path.exists(zip_path):
                os.remove(zip_path)
            if os.path.exists(extract_dir):
                shutil.rmtree(extract_dir, ignore_errors=True)
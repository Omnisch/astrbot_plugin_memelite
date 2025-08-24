import asyncio
import base64
from pathlib import Path
import random
import aiohttp
from meme_generator import Meme, get_memes
from meme_generator.download import check_resources
from meme_generator.exception import MemeGeneratorException
from meme_generator.utils import run_sync, render_meme_list

from astrbot import logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.core import AstrBotConfig

import io
from typing import Any, List, Literal, Optional, Tuple
import astrbot.core.message.components as Comp
from astrbot.core.star.filter.event_message_type import EventMessageType
from PIL import Image
from dataclasses import dataclass, field
from .args_dict import args_dict


@dataclass
class MemeProperties:
    disabled: bool = False
    labels: list[Literal["new", "hot"]] = field(default_factory=list)


# TODO 禁用 meme、new 标签、hot 标签


@register(
    "astrbot_plugin_memelite",
    "Omnisch",
    "表情包生成器，轻量化本地部署",
    "2.1.1",
    "https://github.com/Omnisch/astrbot_plugin_memelite",
)
class MemePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.memes_disabled_list: list[str] = config.get("memes_disabled_list", [])

        self.memes: list[Meme] = get_memes()
        self.meme_keywords: list = [
            keyword for meme in self.memes for keyword in meme.keywords
        ]

        self.prefix: str = config.get("prefix", "")

        self.fuzzy_match: bool = config.get("fuzzy_match", False)
        self.is_compress_image: bool = config.get("is_compress_image", True)

        self.is_check_resources: bool = config.get("is_check_resources", True)
        if self.is_check_resources:
            logger.info("正在检查 memes 资源文件...")
            asyncio.create_task(check_resources())

    @filter.command_group("meme")
    def meme(self):
        """表情包生成器"""
        pass
    
    @meme.command("list")
    async def list(self, event: AstrMessageEvent):
        """查看关键词列表"""
        meme_list: List[Tuple[Meme, Optional[MemeProperties]]] = [
            (meme, MemeProperties(labels=[])) for meme in self.memes
        ]
        text_template = "{index}.{keywords}"
        image_io: io.BytesIO = render_meme_list(
            meme_list=meme_list,  # type: ignore
            text_template=text_template,
            add_category_icon=True,
        )
        yield event.chain_result([Comp.Image.fromBytes(image_io.getvalue())])

    @meme.command("help")
    async def show_details(
        self, event: AstrMessageEvent, keyword: str | int | None = None
    ):
        """查看指定表情需要的参数"""
        if not keyword:
            yield event.plain_result("未指定表情")
            return
        keyword = str(keyword)
        target_keyword = next((k for k in self.meme_keywords if k == keyword), None)
        if target_keyword is None:
            yield event.plain_result("不支持的表情")
            return

        # 匹配 meme
        meme = self._find_meme(keyword)
        if not meme:
            yield event.plain_result("未找到表情")
            return

        # 提取 meme 的所有参数
        name = meme.key
        params_type = meme.params_type
        keywords = meme.keywords
        min_images = params_type.min_images
        max_images = params_type.max_images
        min_texts = params_type.min_texts
        max_texts = params_type.max_texts
        default_texts = params_type.default_texts
        # tags = meme.tags

        meme_info = ""
        if name:
            meme_info += f"键值: {name}\n"

        if keywords:
            meme_info += f"名称: {keywords}\n"

        if max_images > 0:
            meme_info += (
                f"所需图片: {min_images} 张\n"
                if min_images == max_images
                else f"所需图片: {min_images}~{max_images} 张\n"
            )

        if max_texts > 0:
            meme_info += (
                f"所需文本: {min_texts} 段\n"
                if min_texts == max_texts
                else f"所需文本: {min_texts}~{max_texts} 段\n"
            )

        if default_texts:
            meme_info += f"默认文本: {default_texts}\n"

        # if tags:
        #     meme_info += f"标签: {list(tags)}\n"

        args_type = getattr(params_type, "args_type", None)
        if args_type:
            meme_info += "其他参数 (使用下划线使用默认):\n"
            fields = args_type.args_model.__annotations__
            for field_name in fields.keys():
                field = args_type.args_model.__fields__[field_name]
                meme_info += f"- {field.description or '无描述'}"
                meme_info += f" (默认为 {field.default})\n"

        preview: bytes = meme.generate_preview().getvalue()  # type: ignore
        chain = [
            Comp.Plain(meme_info),
            Comp.Image.fromBytes(preview),
        ]
        yield event.chain_result(chain)

    @meme.command("disable")
    async def add_supervisor(
        self, event: AstrMessageEvent, meme_name: str | None = None
    ):
        """禁用表情"""
        if not meme_name:
            yield event.plain_result("未指定要禁用的表情")
            return
        if meme_name not in self.meme_keywords:
            yield event.plain_result(f"表情: {meme_name}不存在")
            return
        if meme_name in self.memes_disabled_list:
            yield event.plain_result(f"表情: {meme_name}已被禁用")
            return
        self.memes_disabled_list.append(meme_name)
        self.config.save_config(replace_config=self.config)
        yield event.plain_result(f"已禁用表情: {meme_name}")
        logger.info(f"当前禁用表情: {self.config['memes_disabled_list']}")

    @meme.command("enable")
    async def remove_supervisor(
        self, event: AstrMessageEvent, meme_name: str | None = None
    ):
        """启用表情"""
        if not meme_name:
            yield event.plain_result("未指定要启用的表情")
            return
        if meme_name not in self.meme_keywords:
            yield event.plain_result(f"表情: {meme_name}不存在")
            return
        if meme_name not in self.memes_disabled_list:
            yield event.plain_result(f"表情: {meme_name}未被禁用")
            return
        self.memes_disabled_list.remove(meme_name)
        self.config.save_config(replace_config=self.config)
        yield event.plain_result(f"已启用表情: {meme_name}")

    @meme.command("blacklist")
    async def list_supervisors(self, event: AstrMessageEvent):
        """查看禁用的表情"""
        yield event.plain_result(f"当前禁用的表情: {self.memes_disabled_list}")

    @filter.event_message_type(EventMessageType.ALL)
    async def meme_handle(self, event: AstrMessageEvent):
        """
        处理表情生成的主流程。

        功能描述：
        - 支持匹配所有表情关键词。
        - 支持从原始消息中提取参数, 空格隔开参数。
        - 支持引用消息传参。
        - 自动获取消息发送者、被 @ 的用户以及 bot 自身的相关参数。
        """

        # 前缀模式
        if self.prefix:
            chain = event.get_messages()
            if not chain:
                return
            first_seg = chain[0]
            # 前缀触发
            if isinstance(first_seg, Comp.Plain):
                if not first_seg.text.startswith(self.prefix):
                    return
            elif isinstance(first_seg, Comp.Reply) and len(chain) > 1:
                second_seg = chain[1]
                if isinstance(
                    second_seg, Comp.Plain
                ) and not second_seg.text.startswith(self.prefix):
                    return
            # @bot 触发
            elif isinstance(first_seg, Comp.At):
                if str(first_seg.qq) != str(event.get_self_id()):
                    return
            else:
                return

        message_str = event.get_message_str().removeprefix(self.prefix)
        if not message_str:
            return

        if self.fuzzy_match:
            # 模糊匹配：检查关键词是否在消息字符串中
            keyword = next((k for k in self.meme_keywords if k in message_str), None)
        else:
            # 精确匹配：检查关键词是否等于消息字符串的第一个单词
            keyword = next(
                (k for k in self.meme_keywords if k == message_str.split()[0]), None
            )

        if not keyword or keyword in self.memes_disabled_list:
            return

        # 匹配表情
        meme = self._find_meme(keyword)
        if not meme:
            yield event.plain_result("未找到相关表情")
            return

        # 收集参数
        images, texts, options = await self._get_params(event, keyword, meme)

        # 合成表情
        try:
            image_io = await run_sync(meme)(images=images, texts=texts, args=options)

        except MemeGeneratorException as e:
            logger.error(e.message)
            return

        # 默认使用原始图片
        image = image_io

        # 如果启用压缩，则尝试压缩图片
        if self.is_compress_image:
            try:
                # 如果压缩成功，则使用压缩后的图片，否则 image 保持为原始图片
                compressed = self.compress_image(image_io)
                if compressed:
                    image = compressed
            except Exception as e:
                logger.warning(f"图片压缩失败，将发送原图: {e}")

        # 发送图片
        chain = [Comp.Image.fromBytes(image.getvalue())]
        yield event.chain_result(chain)  # type: ignore

    def _find_meme(self, keyword: str) -> Meme | None:
        """根据关键词寻找表情"""
        for meme in self.memes:
            if keyword == meme.key or any(k == keyword for k in meme.keywords):
                return meme

    async def _get_params(self, event: AstrMessageEvent, keyword: str, meme: Meme):
        """收集参数"""
        images: list[bytes] = []
        texts: List[str] = []
        options: dict[str, Any] = {}

        params_type = meme.params_type
        min_images = params_type.min_images  # noqa: F841
        max_images = params_type.max_images
        min_texts = params_type.min_texts
        max_texts = params_type.max_texts
        default_texts = params_type.default_texts

        messages = event.get_messages()
        send_id: str = event.get_sender_id()
        self_id: str = event.get_self_id()
        sender_name: str = event.get_sender_name()

        target_ids: list[str] = []
        target_names: list[str] = []

        async def _process_segment(_seg):
            """从消息段中获取参数"""
            if isinstance(_seg, Comp.Image):
                if hasattr(_seg, "url") and _seg.url:
                    img_url = _seg.url
                    # 如果是有效的本地路径，则直接读取文件
                    if Path(img_url).is_file():
                        with open(img_url, "rb") as f:
                            images.append(f.read())
                    else:  # 否则尝试作为URL下载
                        if msg_image := await self.download_image(img_url):
                            images.append(msg_image)

                elif hasattr(_seg, "file"):
                    file_content = _seg.file
                    if isinstance(file_content, str):
                        # 如果是有效的本地路径，则直接读取文件
                        if Path(file_content).is_file():
                            with open(file_content, "rb") as f:
                                images.append(f.read())
                        else:  # 否则尝试作为Base64编码解析
                            if file_content.startswith("base64://"):
                                file_content = file_content[len("base64://") :]
                            file_content = base64.b64decode(file_content)
                    if isinstance(file_content, bytes):
                        images.append(file_content)

            elif isinstance(_seg, Comp.At):
                seg_qq = str(_seg.qq)
                if seg_qq != self_id:
                    target_ids.append(seg_qq)
                    if at_avatar := await self.get_avatar(event, seg_qq):
                        images.append(at_avatar)
                    # 从消息平台获取 At 者的额外参数
                    if result := await self._get_extra(event, target_id=seg_qq):
                        nickname, sex = result
                        options["user_infos"] = [{"name": nickname, "gender": sex}]
                        target_names.append(nickname)

            elif isinstance(_seg, Comp.Plain):
                plains: list[str] = _seg.text.strip().split()
                param_index = 0
                for text in plains:
                    text = text.removeprefix(self.prefix).removeprefix(keyword)
                    if not text:
                        continue
                    # 如果文本被引号包裹，则解析为文本参数
                    if text.startswith("\"") and text.endswith("\""):
                        texts.append(text[1:-1])
                    # 解析 @qq
                    elif text.startswith("@"):
                        target_id = text[1:]
                        if target_id.isdigit():
                            target_ids.append(target_id)
                            if at_avatar := await self.get_avatar(event, target_id):
                                images.append(at_avatar)
                            if result := await self._get_extra(event, target_id=target_id):
                                nickname, sex = result
                                options["user_infos"] = [{"name": nickname, "gender": sex}]
                                target_names.append(nickname)
                    # 解析其他参数
                    elif args_type := params_type.args_type:
                        field_names = list(args_type.args_model.__annotations__.keys())
                        if len(field_names) > param_index:
                            # 下划线使用默认值
                            if text != "_":
                                # 替换常用中文参数
                                if text in args_dict:
                                    text = args_dict[text]
                                logger.info(
                                    f"参数 {field_names[param_index]} 使用 {text}"
                                )
                                options[field_names[param_index]] = text
                            param_index += 1



        # 如果有引用消息，也遍历之
        reply_seg = next((seg for seg in messages if isinstance(seg, Comp.Reply)), None)
        if reply_seg and reply_seg.chain:
            for seg in reply_seg.chain:
                await _process_segment(seg)

        # 遍历原始消息段落
        for seg in messages:
            await _process_segment(seg)

        # 从消息平台获取发送者的额外参数
        if not target_ids:
            if result := await self._get_extra(event, target_id=send_id):
                nickname, sex = result
                options["user_infos"] = [{"name": nickname, "gender": sex}]
                target_names.append(nickname)

        if not target_names:
            target_names.append(sender_name)

        # 确保图片数量在 min_images 到 max_images 之间 (参数足够即可)
        if len(images) < min_images:
            if use_avatar := await self.get_avatar(event, send_id):
                images.insert(0, use_avatar)
        if len(images) < min_images:
            if bot_avatar := await self.get_avatar(event, self_id):
                images.insert(0, bot_avatar)
        meme_images = images[:max_images]

        # 确保文本数量在 min_texts 到 max_texts 之间 (参数足够即可)
        if len(texts) < min_texts and target_names:
            texts.extend(target_names)
        if len(texts) < min_texts and default_texts:
            texts.extend(default_texts)
        texts = texts[:max_texts]

        return meme_images, texts, options

    @staticmethod
    async def _get_extra(event: AstrMessageEvent, target_id: str):
        """从消息平台获取参数"""
        if event.get_platform_name() == "aiocqhttp":
            from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
                AiocqhttpMessageEvent,
            )

            assert isinstance(event, AiocqhttpMessageEvent)
            client = event.bot
            user_info = await client.get_stranger_info(user_id=int(target_id))
            nickname = user_info.get("nickname")
            sex = user_info.get("sex")
            return nickname, sex
        # TODO 适配更多消息平台

    @staticmethod
    def compress_image(image_io: io.BytesIO, max_size: int = 512) -> io.BytesIO | None:
        """压缩静态图片或 GIF 到 max_size 大小"""
        try:
            # 将输入的 bytes 加载为图片
            img = Image.open(image_io)
            output = io.BytesIO()

            if img.format == "GIF":
                return
            else:
                # 如果是静态图片，检查尺寸并压缩
                if img.width > max_size or img.height > max_size:
                    img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
                # 保存处理后的图片到内存中的 BytesIO 对象
                img.save(output, format=img.format)

            # 返回处理后的图片数据 (bytes)
            return output

        except Exception as e:
            raise ValueError(f"图片压缩失败: {e}")

    @staticmethod
    async def download_image(url: str) -> bytes | None:
        """下载图片"""
        url = url.replace("https://", "http://")
        try:
            async with aiohttp.ClientSession() as client:
                response = await client.get(url)
                img_bytes = await response.read()
                return img_bytes
        except Exception as e:
            logger.error(f"图片下载失败: {e}")

    @staticmethod
    async def get_avatar(event: AstrMessageEvent, user_id: str) -> bytes | None:
        """下载头像"""
        # if event.get_platform_name() == "aiocqhttp":
        if not user_id.isdigit():
            user_id = "".join(random.choices("0123456789", k=9))
        avatar_url = f"https://q4.qlogo.cn/headimg_dl?dst_uin={user_id}&spec=640"
        try:
            async with aiohttp.ClientSession() as client:
                response = await client.get(avatar_url, timeout=10)
                response.raise_for_status()
                return await response.read()
        except Exception as e:
            logger.error(f"下载头像失败: {e}")
            return None

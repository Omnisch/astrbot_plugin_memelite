import asyncio
from astrbot import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star, register
from astrbot.core import AstrBotConfig
from astrbot.core.platform import AstrMessageEvent
import astrbot.core.message.components as Comp
from astrbot.core.star.filter.event_message_type import EventMessageType
from .core.param import ParamsCollector
from .core.meme import MemeManager
from .utils import compress_image


@register("astrbot_plugin_memelite", "Zhalslar", "...", "...")
class MemePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.conf = config
        self.collector = ParamsCollector(config)
        self.manager = MemeManager(config, self.collector)

    async def initialize(self):
        await self.manager.check_resources()

    @filter.command_group("meme")
    def meme(self):
        pass

    @meme.command("list", alias={"菜单", "列表"})
    async def meme_list(self, event: AstrMessageEvent):
        """查看关键词列表"""
        if output := await self.manager.render_meme_list_image():
            yield event.chain_result([Comp.Image.fromBytes(output)])
        else:
            yield event.plain_result("meme 列表图生成失败")

    @meme.command("help", alias={"帮助", "详情", "信息", "参数"})
    async def meme_help(
        self, event: AstrMessageEvent, keyword: str | int | None = None
    ):
        """查看指定 meme 需要的参数"""
        if not keyword:
            yield event.plain_result(
                "Memelite 使用帮助\n"
                "- /meme help <关键词> - 查看指定 meme 需要的参数\n"
                "- /meme list - 查看关键词列表\n"
                "- /meme disable <关键词> - 禁用 meme\n"
                "- /meme enable <关键词> - 启用 meme\n"
                "- /meme blacklist - 查看被禁用的 meme 列表\n\n"
                "用空格隔开参数，文本参数需用半角引号 (\") 包围"
            )
            return
        keyword = str(keyword)

        result = self.manager.get_meme_info(keyword)
        if not result:
            yield event.plain_result("未找到相关 meme")
            return

        meme_info, preview = result
        chain = [
            Comp.Plain(meme_info),
            Comp.Image.fromBytes(preview),
        ]
        yield event.chain_result(chain)

    @meme.command("disable", alias={"禁用"})
    async def add_supervisor(
        self, event: AstrMessageEvent, meme_name: str | None = None
    ):
        """禁用 meme"""
        if not meme_name:
            yield event.plain_result("未指定要禁用的 meme")
            return
        if not self.manager.is_meme_keyword(meme_name):
            yield event.plain_result(f"meme: {meme_name} 不存在")
            return
        if meme_name in self.conf["memes_disabled_list"]:
            yield event.plain_result(f"meme: {meme_name} 未启用")
            return
        self.conf["memes_disabled_list"].append(meme_name)
        self.conf.save_config()
        yield event.plain_result(f"已禁用 meme: {meme_name}")
        logger.info(f"当前禁用 meme: {self.conf['memes_disabled_list']}")

    @meme.command("enable", alias={"启用"})
    async def remove_supervisor(
        self, event: AstrMessageEvent, meme_name: str | None = None
    ):
        """启用 meme"""
        if not meme_name:
            yield event.plain_result("未指定要启用的 meme")
            return
        if not self.manager.is_meme_keyword(meme_name):
            yield event.plain_result(f"meme: {meme_name} 不存在")
            return
        if meme_name not in self.conf["memes_disabled_list"]:
            yield event.plain_result(f"meme: {meme_name} 未禁用")
            return
        self.conf["memes_disabled_list"].remove(meme_name)
        self.conf.save_config()
        yield event.plain_result(f"已启用 meme: {meme_name}")

    @meme.command("blacklist", alias={"黑名单", "禁用列表"})
    async def list_supervisors(self, event: AstrMessageEvent):
        """查看被禁用的 meme 列表"""
        yield event.plain_result(f"当前禁用的 meme: {self.conf['memes_disabled_list']}")

    @filter.event_message_type(EventMessageType.ALL)
    async def meme_handle(self, event: AstrMessageEvent):
        """处理 meme 生成的主流程"""
        if self.conf["need_prefix"] and not event.is_at_or_wake_command:
            return
        if self.conf["extra_prefix"] and not event.message_str.startswith(
            self.conf["extra_prefix"]
        ):
            return

        param = event.message_str.removeprefix(self.conf["extra_prefix"])
        if not param:
            return
        # 匹配 meme
        keyword = self.manager.match_meme_keyword(
            text=param, fuzzy_match=self.conf["fuzzy_match"]
        )
        if not keyword or keyword in self.conf["memes_disabled_list"]:
            return

        # 合成表情
        try:
            image = await asyncio.wait_for(
                self.manager.generate_meme(event, keyword),
                timeout=self.conf["meme_timeout"],
            )
        except asyncio.TimeoutError:
            logger.warning(f"meme生成超时: {keyword}")
            yield event.plain_result("meme生成超时")
            return
        except Exception as e:
            logger.error(f"meme生成异常: {e}")
            return

        if image and self.conf["is_compress_image"]:
            try:
                image = compress_image(image) or image
            except Exception:
                pass

        if image:
            yield event.chain_result([Comp.Image.fromBytes(image)])  # type: ignore

    async def terminate(self):
        """插件终止时清理调度器"""
        await self.collector.close()

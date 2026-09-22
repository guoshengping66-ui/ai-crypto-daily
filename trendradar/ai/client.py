# coding=utf-8
"""
AI 客户端模块

基于 LiteLLM 的统一 AI 模型接口
支持 100+ AI 提供商（OpenAI、DeepSeek、Gemini、Claude、国内模型等）
"""

import os
from typing import Any, Dict, List

from litellm import completion


class AIClient:
    """统一的 AI 客户端（基于 LiteLLM）"""

    def __init__(self, config: Dict[str, Any]):
        """
        初始化 AI 客户端

        Args:
            config: AI 配置字典
                - MODEL: 模型标识（格式: provider/model_name）
                - API_KEY: API 密钥
                - API_BASE: API 基础 URL（可选）
                - TEMPERATURE: 采样温度
                - MAX_TOKENS: 最大生成 token 数
                - TIMEOUT: 请求超时时间（秒）
                - NUM_RETRIES: 重试次数（可选）
                - FALLBACK_MODELS: 备用模型列表（可选）
        """
        self.model = config.get("MODEL", "deepseek/deepseek-chat")
        self.api_key = config.get("API_KEY") or os.environ.get("AI_API_KEY", "")
        self.api_base = config.get("API_BASE", "")
        self.temperature = config.get("TEMPERATURE", 1.0)
        self.max_tokens = config.get("MAX_TOKENS", 5000)
        self.timeout = config.get("TIMEOUT", 120)
        self.num_retries = config.get("NUM_RETRIES", 2)
        self.fallback_models = config.get("FALLBACK_MODELS", [])
        self.extra_params = config.get("EXTRA_PARAMS", {}) or {}

    def chat(
        self,
        messages: List[Dict[str, str]],
        **kwargs
    ) -> str:
        """
        调用 AI 模型进行对话

        Args:
            messages: 消息列表，格式: [{"role": "system/user/assistant", "content": "..."}]
            **kwargs: 额外参数，会覆盖默认配置

        Returns:
            str: AI 响应内容

        Raises:
            Exception: API 调用失败时抛出异常
        """
        # 构建请求参数
        params = {
            "model": self.model,
            "messages": messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "timeout": kwargs.get("timeout", self.timeout),
            "num_retries": kwargs.get("num_retries", self.num_retries),
        }

        # 添加 API Key
        if self.api_key:
            params["api_key"] = self.api_key

        # 添加 API Base（如果配置了）
        if self.api_base:
            params["api_base"] = self.api_base

        # 添加 max_tokens（如果配置了且不为 0）
        max_tokens = kwargs.get("max_tokens", self.max_tokens)
        if max_tokens and max_tokens > 0:
            params["max_tokens"] = max_tokens

        # 添加 fallback 模型（如果配置了）
        if self.fallback_models:
            params["fallbacks"] = self.fallback_models

        # 允许通过 config.yaml 传递供应商支持的标准扩展参数（例如 top_p）。
        if isinstance(self.extra_params, dict):
            for key, value in self.extra_params.items():
                if key not in params:
                    params[key] = value

        # 合并其他额外参数
        for key, value in kwargs.items():
            if key in {"temperature", "timeout", "num_retries", "max_tokens"}:
                continue
            if key not in params or key in self.extra_params:
                params[key] = value

        # 调用 LiteLLM
        response = completion(**params)

        # 提取响应内容。部分推理模型/兼容端点会把最终答复放在
        # reasoning_content 中；仅在存在明确的 </think> 分界符时提取其后正文，
        # 不把思考过程转发给用户。
        choice = response.choices[0]
        message = choice.message
        content = message.content
        if not content:
            reasoning_content = getattr(message, "reasoning_content", None)
            if isinstance(reasoning_content, str) and "</think>" in reasoning_content:
                final_answer = reasoning_content.rsplit("</think>", 1)[1].strip()
                if final_answer:
                    content = final_answer
                    print("[AI] 已从 reasoning_content 的 </think> 后提取最终答复")

        if not content:
            finish_reason = getattr(choice, "finish_reason", "unknown")
            reasoning_text = str(getattr(message, "reasoning_content", "") or "")
            has_final_separator = "</think>" in reasoning_text
            final_suffix_size = (
                len(reasoning_text.rsplit("</think>", 1)[1].strip())
                if has_final_separator
                else 0
            )
            print(
                "[AI] 模型未返回最终正文："
                f"finish_reason={finish_reason}, reasoning_chars={len(reasoning_text)}, "
                f"final_separator={has_final_separator}, final_suffix_chars={final_suffix_size}"
            )

        # 某些模型/提供商返回 list（内容块）而非 str，统一转为 str
        if isinstance(content, list):
            content = "\n".join(
                item.get("text", str(item)) if isinstance(item, dict) else str(item)
                for item in content
            )
        if isinstance(content, str) and "</think>" in content:
            content = content.rsplit("</think>", 1)[1].strip()
            print("[AI] 已过滤思考标签，只保留 </think> 后的最终答复")
        return content or ""

    def validate_config(self) -> tuple[bool, str]:
        """
        验证配置是否有效

        Returns:
            tuple: (是否有效, 错误信息)
        """
        if not self.model:
            return False, "未配置 AI 模型（model）"

        if not self.api_key:
            return False, "未配置 AI API Key，请在 config.yaml 或环境变量 AI_API_KEY 中设置"

        # 验证模型格式（应该包含 provider/model）
        if "/" not in self.model:
            return False, f"模型格式错误: {self.model}，应为 'provider/model' 格式（如 'deepseek/deepseek-chat'）"

        return True, ""

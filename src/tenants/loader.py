import importlib
import importlib.util
import logging
import os
import yaml
from typing import Dict, Any, List, Type, Optional
from src.interfaces.base_tool import BaseTool

logger = logging.getLogger(__name__)

# Points to the current directory
TENANTS_DIR = os.path.dirname(os.path.abspath(__file__))


def _format_knowledge(knowledge: Any, indent: int = 0) -> str:
    """
    Recursively format a knowledge_base structure into human-readable text.

    Handles nested dicts, lists of dicts, lists of strings, and scalar values.

    Args:
        knowledge: The knowledge_base value (dict, list, or scalar)
        indent: Current indentation level

    Returns:
        Formatted string suitable for injection into an LLM prompt
    """
    prefix = "  " * indent
    lines = []

    if isinstance(knowledge, dict):
        for key, value in knowledge.items():
            label = key.replace("_", " ").title()
            if isinstance(value, (dict, list)):
                lines.append(f"{prefix}{label}:")
                lines.append(_format_knowledge(value, indent + 1))
            else:
                lines.append(f"{prefix}{label}: {value}")

    elif isinstance(knowledge, list):
        for item in knowledge:
            if isinstance(item, dict):
                # List of dicts — format as "key: value" pairs on one line
                parts = [f"{v}" for v in item.values()]
                lines.append(f"{prefix}- {' — '.join(parts)}")
            else:
                # Simple string list
                lines.append(f"{prefix}- {item}")

    else:
        # Scalar value
        lines.append(f"{prefix}{knowledge}")

    return "\n".join(lines)


class TenantLoader:
    @staticmethod
    def load_tenant(tenant_id: str) -> Dict[str, Any]:
        """
        Loads a tenant's config and dynamically imports their tools.
        Returns a context dictionary ready for the LLM.

        Supports:
        - Handler-ref style: tools referenced by dotted Python path in config
        - Legacy style: tools discovered from local tools.py by name
        - Mixed: both in the same enabled_tools list
        """
        tenant_path = os.path.join(TENANTS_DIR, tenant_id)
        config_path = os.path.join(tenant_path, "config.yaml")

        if not os.path.exists(config_path):
            raise FileNotFoundError(f"Tenant config not found: {tenant_id}")

        # 1. Load YAML Config
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        # 2. Assemble full prompt (Hybrid Context)
        base_prompt = config["system_prompt"]
        knowledge = config.get("knowledge_base")

        if knowledge:
            knowledge_block = _format_knowledge(knowledge)
            full_prompt = (
                f"{base_prompt}\n"
                f"\n--- Business Information ---\n"
                f"{knowledge_block}"
            )
        else:
            # Backward compatible: no knowledge_base -> use raw prompt
            full_prompt = base_prompt

        # 3. Load Tools (handler-ref + legacy)
        enabled_tools = config.get("enabled_tools", [])
        loaded_tools = TenantLoader._load_tools(tenant_id, tenant_path, enabled_tools)

        # 4. Check for duplicate tool names
        seen_names = set()
        for tool in loaded_tools:
            if tool.name in seen_names:
                raise ValueError(
                    f"Duplicate tool name '{tool.name}' in tenant '{tenant_id}'"
                )
            seen_names.add(tool.name)

        return {
            "tenant_id": tenant_id,
            "system_prompt": full_prompt,
            "voice_settings": config.get("voice_settings", {}),
            "knowledge_base": knowledge,
            "tools": loaded_tools,
        }

    @staticmethod
    def _load_tools(
        tenant_id: str,
        tenant_path: str,
        enabled_tools: list,
    ) -> List[BaseTool]:
        """
        Load tools from either handler references or legacy tools.py.

        enabled_tools can contain:
        - Strings: legacy tool names (loaded from local tools.py)
        - Dicts with 'handler' key: dotted path to a BaseTool subclass
        """
        handler_refs = []
        legacy_names = []

        for entry in enabled_tools:
            if isinstance(entry, dict) and "handler" in entry:
                handler_refs.append(entry)
            elif isinstance(entry, str):
                legacy_names.append(entry)
            else:
                logger.warning(f"[{tenant_id}] Ignoring invalid tool entry: {entry}")

        loaded: List[BaseTool] = []

        # A. Load handler-referenced tools (shared integrations)
        for ref in handler_refs:
            try:
                tool = TenantLoader._load_tool_from_handler(
                    handler_path=ref["handler"],
                    tool_config=ref.get("config", {}),
                )
                loaded.append(tool)
                logger.debug(f"[{tenant_id}] Loaded handler tool: {tool.name}")
            except Exception as e:
                logger.error(
                    f"[{tenant_id}] Failed to load handler tool "
                    f"'{ref.get('handler')}': {e}"
                )

        # B. Load legacy tools from local tools.py
        if legacy_names:
            tools_path = os.path.join(tenant_path, "tools.py")
            if os.path.exists(tools_path):
                legacy_tools = TenantLoader._load_legacy_tools(
                    tenant_id, tools_path, legacy_names
                )
                loaded.extend(legacy_tools)
            else:
                logger.warning(
                    f"[{tenant_id}] Legacy tool names specified but no tools.py found"
                )

        return loaded

    @staticmethod
    def _load_tool_from_handler(
        handler_path: str,
        tool_config: Dict[str, Any],
    ) -> BaseTool:
        """
        Import a tool class from a dotted module path and instantiate with config.

        handler_path: e.g., "src.integrations.mock.tools.MockCheckAvailabilityTool"
        """
        module_path, class_name = handler_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        tool_class = getattr(module, class_name)

        if not (isinstance(tool_class, type) and issubclass(tool_class, BaseTool)):
            raise TypeError(f"{handler_path} is not a BaseTool subclass")

        return tool_class(config=tool_config)

    @staticmethod
    def _load_legacy_tools(
        tenant_id: str,
        tools_path: str,
        enabled_names: List[str],
    ) -> List[BaseTool]:
        """
        Original dynamic loading from a local tools.py file.
        Preserved for backward compatibility.
        """
        spec = importlib.util.spec_from_file_location(
            f"tenants.{tenant_id}.tools", tools_path
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        tools = []
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if (
                isinstance(attr, type)
                and issubclass(attr, BaseTool)
                and attr is not BaseTool
            ):
                tool_instance = attr()
                if tool_instance.name in enabled_names:
                    tools.append(tool_instance)
                    logger.debug(f"[{tenant_id}] Loaded legacy tool: {tool_instance.name}")

        return tools

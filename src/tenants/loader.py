import os
import yaml
import importlib.util
from typing import Dict, Any, List, Type, Optional
from src.interfaces.base_tool import BaseTool

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
        
        Supports both legacy (flat system_prompt) and hybrid configs
        (system_prompt + knowledge_base). If knowledge_base exists,
        it is formatted and appended to the system prompt automatically.
        """
        tenant_path = os.path.join(TENANTS_DIR, tenant_id)
        config_path = os.path.join(tenant_path, "config.yaml")
        tools_path = os.path.join(tenant_path, "tools.py")

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
            # Backward compatible: no knowledge_base → use raw prompt
            full_prompt = base_prompt

        # 3. Dynamic Tool Loading
        loaded_tools: List[BaseTool] = []
        
        if os.path.exists(tools_path) and config.get("enabled_tools"):
            # Import the module dynamically
            spec = importlib.util.spec_from_file_location(f"tenants.{tenant_id}.tools", tools_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Scan module for classes that inherit from BaseTool
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if isinstance(attr, type) and issubclass(attr, BaseTool) and attr is not BaseTool:
                    # Check if this tool is enabled in config
                    tool_instance = attr()
                    if tool_instance.name in config["enabled_tools"]:
                        loaded_tools.append(tool_instance)

        return {
            "tenant_id": tenant_id,
            "system_prompt": full_prompt,
            "voice_settings": config["voice_settings"],
            "knowledge_base": knowledge,  # raw dict for downstream consumers (analytics, intelligence)
            "tools": loaded_tools
        }
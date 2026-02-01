import os
import yaml
import importlib.util
from typing import Dict, Any, List, Type
from src.interfaces.base_tool import BaseTool

# Points to the current directory
TENANTS_DIR = os.path.dirname(os.path.abspath(__file__))

class TenantLoader:
    @staticmethod
    def load_tenant(tenant_id: str) -> Dict[str, Any]:
        """
        Loads a tenant's config and dynamically imports their tools.
        Returns a context dictionary ready for the LLM.
        """
        tenant_path = os.path.join(TENANTS_DIR, tenant_id)
        config_path = os.path.join(tenant_path, "config.yaml")
        tools_path = os.path.join(tenant_path, "tools.py")

        if not os.path.exists(config_path):
            raise FileNotFoundError(f"Tenant config not found: {tenant_id}")

        # 1. Load YAML Config
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        # 2. Dynamic Tool Loading
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
            "system_prompt": config["system_prompt"],
            "voice_settings": config["voice_settings"],
            "tools": loaded_tools
        }
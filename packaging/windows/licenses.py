"""收集冻结环境中的第三方许可，不复制用户配置或运行数据。"""

import importlib.metadata
import json
import shutil
import sys
from pathlib import Path

target = Path(sys.argv[1])
python_license = Path(sys.base_prefix) / "LICENSE.txt"
if python_license.is_file():
    shutil.copy2(python_license, target / "Python-LICENSE.txt")
packages = []
for distribution in importlib.metadata.distributions():
    name = distribution.metadata["Name"]
    packages.append(
        {
            "name": name,
            "version": distribution.version,
            "license": distribution.metadata.get("License-Expression")
            or distribution.metadata.get("License", "See upstream distribution"),
        }
    )
    for item in distribution.files or []:
        if ".dist-info" in str(item) and any(
            word in str(item).lower() for word in ["license", "copying", "notice"]
        ):
            source = Path(distribution.locate_file(item))
            if source.is_file():
                destination = target / name / Path(item).name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
(target / "packages.json").write_text(
    json.dumps(packages, ensure_ascii=False, indent=2), encoding="utf-8"
)

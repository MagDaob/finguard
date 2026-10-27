"""为随包的未签名 PostgreSQL 程序声明进程级 UTF-8，保持原有权限清单。"""

import sys
import xml.dom.minidom
from pathlib import Path

ASM = "urn:schemas-microsoft-com:asm.v3"
SETTINGS = "http://schemas.microsoft.com/SMI/2019/WindowsSettings"


def utf8_manifest(source):
    document = xml.dom.minidom.parseString(source)
    root = document.documentElement
    applications = [
        node
        for node in root.childNodes
        if node.nodeType == node.ELEMENT_NODE
        and node.localName == "application"
        and node.namespaceURI == ASM
    ]
    if applications:
        application = applications[0]
    else:
        application = document.createElementNS(ASM, "application")
        application.setAttribute("xmlns", ASM)
        root.appendChild(application)
    settings = next(
        (
            node
            for node in application.childNodes
            if node.nodeType == node.ELEMENT_NODE and node.localName == "windowsSettings"
        ),
        None,
    )
    if settings is None:
        settings = document.createElementNS(ASM, "windowsSettings")
        application.appendChild(settings)
    for node in list(settings.childNodes):
        if node.nodeType == node.ELEMENT_NODE and node.localName == "activeCodePage":
            settings.removeChild(node)
    encoding = document.createElementNS(SETTINGS, "activeCodePage")
    encoding.setAttribute("xmlns", SETTINGS)
    encoding.appendChild(document.createTextNode("UTF-8"))
    settings.appendChild(encoding)
    return document.toxml(encoding="UTF-8")


def main():
    import pefile
    from PyInstaller.utils.win32 import winmanifest, winresource

    for executable in Path(sys.argv[1]).glob("*.exe"):
        with pefile.PE(str(executable), fast_load=True) as image:
            if image.OPTIONAL_HEADER.DATA_DIRECTORY[
                pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_SECURITY"]
            ].VirtualAddress:
                raise RuntimeError("Refuse to modify a signed PostgreSQL executable")
        resources = winresource.get_resources(str(executable), [24])
        source = (
            next(iter(resources[24][1].values()))
            if 24 in resources and 1 in resources[24]
            else b'<assembly xmlns="urn:schemas-microsoft-com:asm.v1" manifestVersion="1.0"/>'
        )
        winmanifest.write_manifest_to_executable(str(executable), utf8_manifest(source))


if __name__ == "__main__":
    main()

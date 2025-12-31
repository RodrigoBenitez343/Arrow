import os
import uuid
import shutil
import xml.etree.ElementTree as ET
from xml.dom import minidom

def generate_guid():
    return str(uuid.uuid4()).upper()

def create_wxs(dist_dir, output_file, app_name, version, manufacturer):
    # Ensure install_dependencies.ps1 is in the dist directory
    script_src = os.path.join(os.path.dirname(__file__), "install_dependencies.ps1")
    script_dest = os.path.join(dist_dir, "install_dependencies.ps1")
    if os.path.exists(script_src):
        shutil.copy2(script_src, script_dest)

    product_code = generate_guid()
    upgrade_code = "77E1E624-9B5A-4B6A-8A1E-8B5B4E6C7D8E" # Persistent upgrade code
    
    wix = ET.Element("Wix", xmlns="http://wixtoolset.org/schemas/v4/wxs", 
                     **{"xmlns:ui": "http://wixtoolset.org/schemas/v4/wxs/ui"})
    package = ET.SubElement(wix, "Package", 
                            Name=app_name, 
                            Manufacturer=manufacturer, 
                            Version=version, 
                            UpgradeCode=upgrade_code,
                            Language="1033",
                            Scope="perUser")
    
    ET.SubElement(package, "MajorUpgrade", DowngradeErrorMessage="A newer version of LoOper is already installed.")
    
    # Media template for embedding files in MSI
    ET.SubElement(package, "MediaTemplate", EmbedCab="yes")

    # --- Dependency Detection ---
    # Detect Ollama
    ollama_search = ET.SubElement(package, "Property", Id="OLLAMA_PATH")
    ET.SubElement(ollama_search, "DirectorySearch", Id="CheckOllamaLocalApp", Path="[LocalAppDataFolder]Ollama")

    # --- UI Configuration ---
    # Use standard WiX UI
    ui_ref = ET.SubElement(package, "ui:WixUI", Id="WixUI_InstallDir", InstallDirectory="INSTALLFOLDER")
    
    # Custom Images and License
    # WixUI_Bmp_Banner (Top banner) - 493x58
    # WixUI_Bmp_Dialog (Welcome/Exit background) - 493x312
    # WixUI_Ico_Install (Wizard Icon)
    
    dialog_img = os.path.join("LoOper", "WixUIDialog.bmp")
    banner_img = os.path.join("LoOper", "WixUIBanner.bmp")
    app_icon = os.path.join("LoOper", "LoOper.ico")
    license_rtf = "License.rtf"

    ET.SubElement(package, "WixVariable", Id="WixUIBannerBmp", Value=banner_img)
    ET.SubElement(package, "WixVariable", Id="WixUIDialogBmp", Value=dialog_img)
    ET.SubElement(package, "WixVariable", Id="WixUILicenseRtf", Value=license_rtf)
    
    # Set the wizard icon
    ET.SubElement(package, "Icon", Id="WizardIcon", SourceFile=app_icon)
    ET.SubElement(package, "Property", Id="ARPPRODUCTICON", Value="WizardIcon")
    
    # Add a custom text on the welcome or a new dialog to show status
    # In WiX 4, we can use WixUI_InstallDir and customize the sequence
    # For simplicity, we'll use a Property to drive the message
    
    ET.SubElement(package, "Property", Id="OLLAMA_STATUS_MSG", Value="Ollama: Not Found (Will be installed)")
    
    # Custom Actions to update the status messages if found
    ET.SubElement(package, "SetProperty", Id="OLLAMA_STATUS_MSG", Value="Ollama: Installed", After="AppSearch", Condition="OLLAMA_PATH")
    
    # Icons and properties
    # icon_path = os.path.join(dist_dir, "LoOper.ico")
    # if os.path.exists(icon_path):
    #     ET.SubElement(package, "Icon", Id="AppIcon", SourceFile=icon_path)
    #     ET.SubElement(package, "Property", Id="ARPPRODUCTICON", Value="AppIcon")

    # Directory structure
    standard_directory = ET.SubElement(package, "StandardDirectory", Id="LocalAppDataFolder")
    install_dir = ET.SubElement(standard_directory, "Directory", Id="INSTALLFOLDER", Name=app_name)
    
    # Shortcut directory
    start_menu_dir = ET.SubElement(package, "StandardDirectory", Id="ProgramMenuFolder")
    app_start_menu_dir = ET.SubElement(start_menu_dir, "Directory", Id="InstallProgramMenuFolder", Name=app_name)
    
    # Desktop directory
    desktop_dir = ET.SubElement(package, "StandardDirectory", Id="DesktopFolder")

    components = []
    
    # Function to recursively add files and directories
    def add_files(current_dir, parent_element, relative_path=""):
        for item in os.listdir(current_dir):
            item_path = os.path.join(current_dir, item)
            item_rel_path = os.path.join(relative_path, item)
            
            if os.path.isdir(item_path):
                # Avoid harvesting some dirs if necessary
                if item in ("__pycache__", ".git"):
                    continue
                dir_id = "dir_" + generate_guid().replace("-", "_")
                new_dir = ET.SubElement(parent_element, "Directory", Id=dir_id, Name=item)
                add_files(item_path, new_dir, item_rel_path)
            else:
                # Exclude development files, large unnecessary libs, and log files
                if item.lower().endswith(('.lib', '.pdb', '.obj', '.exp', '.ilk', '.log', '.wixpdb')):
                    continue
                
                comp_id = "comp_" + generate_guid().replace("-", "_")
                file_id = "file_" + generate_guid().replace("-", "_")
                
                comp = ET.SubElement(parent_element, "Component", Id=comp_id, Guid=generate_guid())
                file_node = ET.SubElement(comp, "File", Id=file_id, Source=item_path, KeyPath="yes")
                
                # Add shortcut for the main executable
                if item == "LoOper.exe" and relative_path == "":
                    # Start Menu Shortcut
                    ET.SubElement(comp, "Shortcut", 
                                             Id="MainShortcut",
                                             Name=app_name,
                                             Directory="InstallProgramMenuFolder",
                                             Target=f"[!{file_id}]",
                                             WorkingDirectory="INSTALLFOLDER")
                    
                    # Desktop Shortcut
                    ET.SubElement(comp, "Shortcut", 
                                             Id="DesktopShortcut",
                                             Name=app_name,
                                             Directory="DesktopFolder",
                                             Target=f"[!{file_id}]",
                                             WorkingDirectory="INSTALLFOLDER")
                
                components.append(comp_id)

    add_files(dist_dir, install_dir)
    
    # Feature
    feature = ET.SubElement(package, "Feature", Id="MainFeature", Title=app_name, Level="1")
    for comp_id in components:
        ET.SubElement(feature, "ComponentRef", Id=comp_id)
    
    # Add ProgramMenuFolder component for shortcut cleanup
    cleanup_comp_id = "CleanupShortcut"
    cleanup_comp = ET.SubElement(app_start_menu_dir, "Component", Id=cleanup_comp_id, Guid=generate_guid())
    ET.SubElement(cleanup_comp, "RemoveFolder", Id="RemoveInstallProgramMenuFolder", On="uninstall")
    ET.SubElement(cleanup_comp, "RegistryValue", Root="HKCU", Key=f"Software\\{manufacturer}\\{app_name}", Name="installed", Type="integer", Value="1", KeyPath="yes")
    ET.SubElement(feature, "ComponentRef", Id=cleanup_comp_id)

    # Custom Action to run dependency installer
    # We run it at the end of the installation
    custom_action_id = "RunDependencyInstaller"
    ET.SubElement(package, "CustomAction", 
                  Id=custom_action_id, 
                  Directory="INSTALLFOLDER", 
                  ExeCommand='powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File "[INSTALLFOLDER]install_dependencies.ps1"',
                  Execute="immediate",
                  Return="asyncNoWait")

    install_exec_seq = ET.SubElement(package, "InstallExecuteSequence")
    ET.SubElement(install_exec_seq, "Custom", Action=custom_action_id, After="InstallFinalize", Condition="NOT Installed")

    # Pretty print
    xml_str = ET.tostring(wix, encoding='utf-8')
    reparsed = minidom.parseString(xml_str)
    with open(output_file, "w", encoding='utf-8') as f:
        f.write(reparsed.toprettyxml(indent="  "))

if __name__ == "__main__":
    dist_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "dist", "LoOper"))
    output_wxs = os.path.abspath(os.path.join(os.path.dirname(__file__), "LoOper.wxs"))
    create_wxs(dist_path, output_wxs, "LoOper", "1.0.0", "Rodrigo David Benitez")
    print(f"Generated {output_wxs}")

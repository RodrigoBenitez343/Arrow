import os
import uuid
import shutil
import zipfile
import xml.etree.ElementTree as ET
from xml.dom import minidom

def generate_guid():
    return str(uuid.uuid4()).upper()

def download_and_extract_rdpwrap(extract_to):
    rdpwrap_bin = os.path.join(extract_to, "RDPWrap")
    if os.path.exists(rdpwrap_bin):
        return rdpwrap_bin

    os.makedirs(rdpwrap_bin, exist_ok=True)
    zip_path = os.path.join(extract_to, "RDPWrap-v1.6.2.zip")

    if not os.path.exists(zip_path):
        import urllib.request
        url = "https://github.com/stascorp/rdpwrap/releases/download/v1.6.2/RDPWrap-v1.6.2.zip"
        print(f"Downloading RDPWrap from {url}...")
        urllib.request.urlretrieve(url, zip_path)
        print("Download complete.")

    print(f"Extracting RDPWrap to {rdpwrap_bin}...")
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(rdpwrap_bin)

    os.remove(zip_path)

    bin_dir = os.path.join(rdpwrap_bin, "bin")
    if os.path.exists(bin_dir):
        for f in os.listdir(bin_dir):
            src = os.path.join(bin_dir, f)
            dst = os.path.join(rdpwrap_bin, f)
            if not os.path.exists(dst):
                shutil.move(src, dst)
        if os.path.exists(bin_dir) and not os.listdir(bin_dir):
            os.rmdir(bin_dir)

    return rdpwrap_bin

def create_wxs(dist_dir, output_file, app_name, version, manufacturer):
    # Ensure install_dependencies.ps1 is in the dist directory
    script_src = os.path.join(os.path.dirname(__file__), "install_dependencies.ps1")
    script_dest = os.path.join(dist_dir, "install_dependencies.ps1")
    if os.path.exists(script_src):
        os.makedirs(dist_dir, exist_ok=True)
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
    
    ET.SubElement(package, "MajorUpgrade", DowngradeErrorMessage="A newer version of arrow is already installed.")
    
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

    def add_files(current_dir, parent_element, relative_path="", output_file_rel_base=None):
        nonlocal components
        if output_file_rel_base is None:
            output_file_rel_base = os.path.dirname(output_file)
        for item in os.listdir(current_dir):
            item_path = os.path.join(current_dir, item)
            item_rel_path = os.path.join(relative_path, item)

            if os.path.isdir(item_path):
                if item in ("__pycache__", ".git"):
                    continue
                dir_id = "dir_" + generate_guid().replace("-", "_")
                new_dir = ET.SubElement(parent_element, "Directory", Id=dir_id, Name=item)
                add_files(item_path, new_dir, item_rel_path, output_file_rel_base)
            else:
                if item.lower().endswith(('.lib', '.pdb', '.obj', '.exp', '.ilk', '.log', '.wixpdb')):
                    continue

                comp_id = "comp_" + generate_guid().replace("-", "_")
                file_id = "file_" + generate_guid().replace("-", "_")

                source_rel_path = os.path.relpath(item_path, output_file_rel_base).replace(os.sep, '/')
                comp = ET.SubElement(parent_element, "Component", Id=comp_id, Guid=generate_guid())
                file_node = ET.SubElement(comp, "File", Id=file_id, Source=source_rel_path, KeyPath="yes")

                if item == "LoOper.exe" and relative_path == "":
                    ET.SubElement(comp, "Shortcut",
                                             Id="MainShortcut",
                                             Name=app_name,
                                             Directory="InstallProgramMenuFolder",
                                             Target=f"[!{file_id}]",
                                             WorkingDirectory="INSTALLFOLDER")

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

    # Custom Action: validate license key BEFORE file extraction.
    # We reference the validator via [SourceDir] because at UI-sequence time
    # the MSI source directory is available but the files haven't been copied
    # to INSTALLFOLDER yet.
    validate_license_action_id = "ValidateLicense"
    ET.SubElement(package, "CustomAction",
                  Id=validate_license_action_id,
                  Directory="INSTALLFOLDER",
                  ExeCommand='"[SourceDir]license_validator.exe" --install "[INSTALLFOLDER]"',
                  Execute="immediate",
                  Return="check")

    # InstallUISequence runs the validation early (before file extraction)
    # In WiX 4, UI sequence custom actions are placed here.
    ui_seq = ET.SubElement(package, "InstallUISequence")
    ET.SubElement(ui_seq, "Custom", Action=validate_license_action_id, Before="ProgressDlg", Condition="NOT Installed")

    # Custom Action to install RDPWrap
    rdpwrap_install_action_id = "InstallRDPWrap"
    ET.SubElement(package, "CustomAction",
                  Id=rdpwrap_install_action_id,
                  Directory="INSTALLFOLDER",
                  ExeCommand='powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -Command "& { if (Test-Path \\"[INSTALLFOLDER]RDPWrap\\") { \\"& \\"[INSTALLFOLDER]RDPWrap\\RDPWInst.exe\\" -i -o; & \\"[INSTALLFOLDER]RDPWrap\\RDPWInst.exe\\" -w; if (Test-Path \\"C:\\Program Files\\RDP Wrapper\\rdpwrap.ini\\") { Invoke-WebRequest -Uri \\"https://raw.githubusercontent.com/sebaxakerhtc/rdpwrap.ini/master/rdpwrap.ini\\" -OutFile \\"C:\\Program Files\\RDP Wrapper\\rdpwrap.ini\\" -UseBasicParsing }; Stop-Service TermService -ErrorAction SilentlyContinue; Start-Service TermService -ErrorAction SilentlyContinue } }"',
                  Execute="immediate",
                  Return="asyncNoWait")

    install_exec_seq = ET.SubElement(package, "InstallExecuteSequence")
    ET.SubElement(install_exec_seq, "Custom", Action=custom_action_id, After="InstallFinalize", Condition="NOT Installed")
    ET.SubElement(install_exec_seq, "Custom", Action=rdpwrap_install_action_id, After="RunDependencyInstaller", Condition="NOT Installed")

    # Pretty print
    xml_str = ET.tostring(wix, encoding='utf-8')
    reparsed = minidom.parseString(xml_str)
    with open(output_file, "w", encoding='utf-8') as f:
        f.write(reparsed.toprettyxml(indent="  "))

if __name__ == "__main__":
    dist_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "dist", "arrow"))
    output_wxs = os.path.abspath(os.path.join(os.path.dirname(__file__), "arrow.wxs"))

    print("Preparing RDPWrap for installer...")
    rdpwrap_src = download_and_extract_rdpwrap(os.path.dirname(dist_path))
    rdpwrap_dest = os.path.join(dist_path, "RDPWrap")
    if os.path.exists(rdpwrap_dest):
        shutil.rmtree(rdpwrap_dest)
    shutil.copytree(rdpwrap_src, rdpwrap_dest)
    print(f"RDPWrap prepared at {rdpwrap_dest}")

    create_wxs(dist_path, output_wxs, "arrow", "1.1.0", "Rodrigo David Benitez")
    print(f"Generated {output_wxs}")

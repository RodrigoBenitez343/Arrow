import os
import sys
import subprocess
import ctypes
import shutil
import urllib.request
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def install_rdpwrap():
    rdpwrap_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "utils", "rdpwrap"))
    install_bat = os.path.join(rdpwrap_dir, "bin", "install.bat")
    update_bat = os.path.join(rdpwrap_dir, "bin", "update.bat")
    rdpwinst_exe = os.path.join(rdpwrap_dir, "bin", "RDPWInst.exe")
    
    if not os.path.exists(install_bat):
        logger.error(f"RDPWrap install script not found at: {install_bat}")
        return False
        
    # The source repository doesn't include the pre-compiled binary. We need to download it.
    if not os.path.exists(rdpwinst_exe):
        logger.info("Downloading RDPWInst.exe binary from GitHub Releases...")
        try:
            import zipfile
            import io
            
            # Download the latest release zip
            url = "https://github.com/stascorp/rdpwrap/releases/download/v1.6.2/RDPWrap-v1.6.2.zip"
            zip_path = os.path.join(rdpwrap_dir, "bin", "RDPWrap-v1.6.2.zip")
            urllib.request.urlretrieve(url, zip_path)
            
            # Extract just the executables to the bin folder
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                for file_info in zip_ref.infolist():
                    if file_info.filename.endswith('.exe'):
                        zip_ref.extract(file_info, os.path.join(rdpwrap_dir, "bin"))
                        
            # Clean up zip
            os.remove(zip_path)
            logger.info("Successfully downloaded and extracted RDPWrap binaries.")
        except Exception as e:
            logger.error(f"Failed to download RDPWrap binaries: {e}")
            logger.error("Please manually download https://github.com/stascorp/rdpwrap/releases/download/v1.6.2/RDPWrap-v1.6.2.zip and extract RDPWInst.exe into d:\\LoOperV2\\utils\\rdpwrap\\bin\\")
            return False
            
    logger.info("Installing RDPWrap...")
    try:
        # Run install.bat - we don't capture output because install.bat sometimes prompts the user
        # or runs commands that hang if stdout/stderr pipes are blocked.
        result = subprocess.run([install_bat], cwd=os.path.dirname(install_bat))
        if result.returncode != 0:
            logger.warning(f"install.bat returned non-zero code: {result.returncode}")
            
        # Run update.bat to fetch latest ini (crucial for newer Windows builds)
        if os.path.exists(update_bat):
            logger.info("Updating RDPWrap configuration (rdpwrap.ini)...")
            subprocess.run([update_bat], cwd=os.path.dirname(update_bat))
            
        # Optional: Force download latest community ini if update.bat fails
        ini_path = r"C:\Program Files\RDP Wrapper\rdpwrap.ini"
        if os.path.exists(r"C:\Program Files\RDP Wrapper"):
            try:
                logger.info("Fetching latest community rdpwrap.ini...")
                url = "https://raw.githubusercontent.com/sebaxakerhtc/rdpwrap.ini/master/rdpwrap.ini"
                urllib.request.urlretrieve(url, ini_path)
                # Restart TermService to apply new ini
                subprocess.run(["net", "stop", "TermService", "/y"], capture_output=True)
                subprocess.run(["net", "start", "TermService"], capture_output=True)
            except Exception as e:
                logger.warning(f"Failed to fetch community ini: {e}")
                
        logger.info("RDPWrap installation complete.")
        return True
    except Exception as e:
        logger.error(f"Failed to install RDPWrap: {e}")
        return False

def create_agent_user():
    username = "LoOperAgent"
    password = "LoOperPassword123!" # Default strong password required by Windows policy
    
    logger.info(f"Checking for user account: {username}")
    
    # Check if user exists
    check_user = subprocess.run(["net", "user", username], capture_output=True)
    if check_user.returncode == 0:
        logger.info(f"User {username} already exists.")
    else:
        logger.info(f"Creating user {username}...")
        create_cmd = f'net user {username} {password} /add /passwordchg:no /expires:never'
        result = subprocess.run(create_cmd, shell=True, capture_output=True, text=True)
        
        if result.returncode != 0:
            logger.error(f"Failed to create user. Error: {result.stderr}")
            return False
            
    # Add to Remote Desktop Users group
    logger.info("Adding user to Remote Desktop Users group...")
    # Group name varies by locale (e.g. "Usuarios de escritorio remoto" in Spanish)
    # We try English first, then lookup SID if needed, but for simplicity we try English
    rdp_group = "Remote Desktop Users"
    
    # Quick trick to find the localized name of Remote Desktop Users group using SID S-1-5-32-544
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList")
        # Just fallback to trying to add it
    except: pass
    
    # In PowerShell we can use SID to be locale independent
    ps_cmd = f'$objSID = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-32-555"); $objGroup = $objSID.Translate([System.Security.Principal.NTAccount]); net localgroup $objGroup.Value.Split("\\")[1] {username} /add'
    subprocess.run(["powershell", "-Command", ps_cmd], capture_output=True)
    
    # CRITICAL: Add user to Administrators group so it can bypass UAC and execute bat files in alternate shells
    logger.info("Adding user to Administrators group...")
    ps_cmd_admin = f'$objSID = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-32-544"); $objGroup = $objSID.Translate([System.Security.Principal.NTAccount]); net localgroup $objGroup.Value.Split("\\")[1] {username} /add'
    subprocess.run(["powershell", "-Command", ps_cmd_admin], capture_output=True)
    
    # Allow alternate shell for standard RDP connections
    logger.info("Enabling alternate shell support in Registry...")
    subprocess.run(
        [
            "reg",
            "add",
            r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Terminal Server\TSAppAllowList",
            "/v",
            "fDisabledAllowList",
            "/t",
            "REG_DWORD",
            "/d",
            "1",
            "/f",
        ],
        capture_output=True,
        text=True,
    )
    verify = subprocess.run(
        [
            "reg",
            "query",
            r"HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Terminal Server\TSAppAllowList",
            "/v",
            "fDisabledAllowList",
        ],
        capture_output=True,
        text=True,
    )
    if verify.returncode != 0 or "0x1" not in (verify.stdout or ""):
        logger.error("Failed to enable alternate shell support in Registry.")
    
    logger.info(f"User {username} is ready for RDP connections.")
    return True

def configure_firewall():
    logger.info("Configuring Windows Firewall for RDP...")
    subprocess.run(["netsh", "advfirewall", "firewall", "set", "rule", "group=\"Remote Desktop\"", "new", "enable=Yes"], capture_output=True)
    
def main():
    print("="*50)
    print("LoOper Concurrent RDP Environment Setup")
    print("="*50)
    
    if not is_admin():
        logger.warning("Script is not running as Administrator. Attempting to elevate privileges...")
        try:
            # Re-run the script with admin rights
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, f'"{os.path.abspath(__file__)}"', None, 1
            )
            sys.exit(0)
        except Exception as e:
            logger.error(f"Failed to elevate privileges: {e}")
            logger.error("Please right-click your terminal and select 'Run as Administrator'.")
            sys.exit(1)
        
    print("\nThis script will:")
    print("1. Install RDPWrap to allow hidden background automation sessions.")
    print("2. Create a local Windows account named 'LoOperAgent'.")
    print("3. Configure Firewall and Remote Desktop settings.\n")
    
    choice = input("Do you want to proceed? (y/n): ")
    if choice.lower() != 'y':
        print("Setup aborted.")
        sys.exit(0)
        
    install_rdpwrap()
    create_agent_user()
    configure_firewall()
    
    print("\n" + "="*50)
    print("SETUP COMPLETE!")
    print("="*50)
    print("IMPORTANT: You must configure LoOper to use the following credentials:")
    print("Username: LoOperAgent")
    print("Password: LoOperPassword123!")
    print("\nYou can now run your automation chains in the background without interrupting your mouse!")

if __name__ == "__main__":
    main()

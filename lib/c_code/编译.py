#!/usr/bin/env python3
"""
C/C++源文件编译工具 - 完整版
支持单个文件和库项目的编译，支持交叉编译
输出为系统架构命名的共享库，供Python调用
新增：自动配置交叉编译、自动安装编译器、支持多目标编译
"""

import os
import sys
import platform
import subprocess
import json
import time
import shutil
from pathlib import Path
from datetime import datetime
import argparse
import re
from collections import defaultdict

class CCompiler:
    def __init__(self, config_file=None):
        self.system = platform.system().lower()
        self.arch = self.normalize_arch(platform.machine().lower())
        self.compiler = 'gcc'
        self.lib_suffix = '_lib'
        self.output_dir = 'c'
        self.compile_history = []
        self.config = self.load_config(config_file)
        self.setup_environment()
        self.cross_compile_enabled = False
        self.target_arch = ''
        self.target_system = ''
        
        # 多目标编译支持
        self.multi_target_enabled = False
        self.targets = []  # 存储多个目标配置
        
    def normalize_arch(self, arch):
        """标准化架构名称"""
        arch = arch.lower()
        if arch in ['x86_64', 'amd64', 'x64']:
            return 'x64'
        elif arch in ['i386', 'i686', 'x86']:
            return 'x86'
        elif arch in ['arm64', 'aarch64']:
            return 'arm64'
        elif 'arm' in arch:
            return 'arm'
        elif 'mips' in arch:
            return 'mips'
        elif 'powerpc' in arch or 'ppc' in arch:
            return 'ppc'
        elif 'riscv' in arch:
            return 'riscv64'
        else:
            return arch
    
    def get_output_system(self):
        """获取输出目标系统（交叉编译优先）"""
        if self.cross_compile_enabled and self.target_system:
            return self.target_system
        return self.system
    
    def get_output_arch(self):
        """获取输出目标架构（交叉编译优先）"""
        if self.cross_compile_enabled and self.target_arch:
            return self.target_arch
        return self.arch
    
    def load_config(self, config_file):
        """加载配置文件"""
        default_config = {
            'compiler': 'gcc',
            'lib_suffix': '_lib',
            'output_dir': 'c',
            'compile_flags': ['-shared', '-fPIC', '-O2', '-Wall', '-Wextra'],
            'c_flags': ['-std=c99'],
            'cpp_flags': ['-std=c++11'],
            'extra_libs': [],
            'extra_includes': [],
            'cross_compile': {
                'enabled': False,
                'target_system': '',
                'target_arch': '',
                'toolchain_prefix': '',
                'sysroot': '',
                'c_flags': [],
                'cpp_flags': [],
                'link_flags': []
            },
            'multi_target': {
                'enabled': False,
                'targets': []  # 存储多个目标配置
            }
        }
        
        if config_file and Path(config_file).exists():
            try:
                with open(config_file, 'r') as f:
                    user_config = json.load(f)
                    for key, value in user_config.items():
                        if key in default_config and isinstance(value, dict) and isinstance(default_config[key], dict):
                            default_config[key].update(value)
                        else:
                            default_config[key] = value
                    print(f"✓ 加载配置文件: {config_file}")
            except Exception as e:
                print(f"✗ 配置文件错误: {e}")
        
        return default_config
    
    def save_config(self, config_file='compile_config.json'):
        """保存配置"""
        try:
            # 更新配置
            self.config['multi_target']['enabled'] = self.multi_target_enabled
            self.config['multi_target']['targets'] = self.targets
            
            with open(config_file, 'w') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)
            print(f"✓ 配置保存到: {config_file}")
        except Exception as e:
            print(f"✗ 保存配置失败: {e}")
    
    def check_compiler_installed(self, compiler_name):
        """检查编译器是否已安装"""
        try:
            result = subprocess.run([compiler_name, '--version'], 
                                  capture_output=True, text=True, timeout=2)
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
    
    def install_compiler(self, compiler_type='gcc', target_arch=None, target_system=None):
        """自动安装编译器"""
        system = platform.system().lower()
        
        print(f"\n正在安装 {compiler_type} 编译器...")
        
        if system == 'linux':
            return self._install_compiler_linux(compiler_type, target_arch, target_system)
        elif system == 'darwin':
            return self._install_compiler_macos(compiler_type)
        elif system == 'windows':
            return self._install_compiler_windows(compiler_type)
        else:
            print(f"✗ 不支持的操作系统: {system}")
            return False
    
    def _install_compiler_linux(self, compiler_type, target_arch=None, target_system=None):
        """在Linux上安装编译器"""
        if shutil.which('apt'):
            package_manager = 'apt'
            install_cmd = ['sudo', 'apt', 'install', '-y']
            update_cmd = ['sudo', 'apt', 'update']
        elif shutil.which('yum'):
            package_manager = 'yum'
            install_cmd = ['sudo', 'yum', 'install', '-y']
            update_cmd = ['sudo', 'yum', 'check-update']
        elif shutil.which('dnf'):
            package_manager = 'dnf'
            install_cmd = ['sudo', 'dnf', 'install', '-y']
            update_cmd = ['sudo', 'dnf', 'check-update']
        elif shutil.which('pacman'):
            package_manager = 'pacman'
            install_cmd = ['sudo', 'pacman', '-S', '--noconfirm']
            update_cmd = ['sudo', 'pacman', '-Sy']
        else:
            print("✗ 未能检测到支持的包管理器")
            return False
        
        print(f"✓ 检测到包管理器: {package_manager}")
        
        print("\n更新软件包索引...")
        try:
            subprocess.run(update_cmd, check=True, timeout=60)
            print("✓ 软件包索引更新完成")
        except subprocess.TimeoutExpired:
            print("⚠ 软件包索引更新超时，继续安装...")
        except Exception as e:
            print(f"⚠ 软件包索引更新失败: {e}")
        
        packages = []
        
        if compiler_type == 'gcc':
            if package_manager in ['apt', 'yum', 'dnf']:
                if target_arch and target_system:
                    cross_packages = self._get_cross_compiler_packages(target_arch, target_system, package_manager)
                    packages.extend(cross_packages)
                else:
                    if package_manager == 'apt':
                        packages.extend(['gcc', 'g++', 'make'])
                    else:
                        packages.extend(['gcc', 'gcc-c++', 'make'])
        elif compiler_type == 'clang':
            if package_manager == 'apt':
                packages.extend(['clang', 'lld', 'make'])
            elif package_manager in ['yum', 'dnf']:
                packages.extend(['clang', 'lld', 'make'])
            elif package_manager == 'pacman':
                packages.extend(['clang', 'lld', 'make'])
        
        if not packages:
            print(f"✗ 未找到适用于 {package_manager} 的 {compiler_type} 包")
            return False
        
        print(f"\n将安装以下包: {', '.join(packages)}")
        confirm = input("是否继续安装？(y/N): ").strip().lower()
        if confirm != 'y':
            print("已取消安装")
            return False
        
        try:
            cmd = install_cmd + packages
            print(f"\n执行: {' '.join(cmd)}")
            subprocess.run(cmd, check=True, timeout=300)
            print(f"\n✓ {compiler_type} 编译器安装成功!")
            return True
        except subprocess.TimeoutExpired:
            print("✗ 安装超时")
            return False
        except subprocess.CalledProcessError as e:
            print(f"✗ 安装失败: {e}")
            return False
        except Exception as e:
            print(f"✗ 安装异常: {e}")
            return False
    
    def _get_cross_compiler_packages(self, target_arch, target_system, package_manager):
        """获取交叉编译工具链包名"""
        packages = []
        
        if target_system == 'linux':
            arch_map = {
                'arm': 'arm-linux-gnueabihf',
                'arm64': 'aarch64-linux-gnu',
                'mips': 'mips-linux-gnu',
                'mipsel': 'mipsel-linux-gnu',
                'mips64': 'mips64-linux-gnuabi64',
                'ppc': 'powerpc-linux-gnu',
                'ppc64': 'powerpc64-linux-gnu',
                'riscv64': 'riscv64-linux-gnu',
                'loongarch64': 'loongarch64-linux-gnu'
            }
            
            if target_arch in arch_map:
                triplet = arch_map[target_arch]
                if package_manager == 'apt':
                    packages.append(f'gcc-{triplet}')
                    packages.append(f'g++-{triplet}')
                elif package_manager in ['yum', 'dnf']:
                    packages.append(f'gcc-{triplet}')
                    packages.append(f'gcc-c++-{triplet}')
        
        elif target_system == 'windows':
            if package_manager == 'apt':
                packages.extend(['mingw-w64', 'gcc-mingw-w64'])
            elif package_manager in ['yum', 'dnf']:
                packages.extend(['mingw64-gcc', 'mingw32-gcc'])
        
        return packages
    
    def _install_compiler_macos(self, compiler_type):
        """在macOS上安装编译器"""
        if not shutil.which('brew'):
            print("⚠ 未检测到Homebrew，正在安装...")
            install_brew_cmd = '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
            try:
                subprocess.run(install_brew_cmd, shell=True, check=True, timeout=600)
                print("✓ Homebrew安装成功")
            except Exception as e:
                print(f"✗ Homebrew安装失败: {e}")
                return False
        
        if compiler_type == 'gcc':
            packages = ['gcc']
        elif compiler_type == 'clang':
            print("✓ macOS已内置clang")
            return True
        else:
            return False
        
        try:
            cmd = ['brew', 'install'] + packages
            print(f"\n执行: {' '.join(cmd)}")
            subprocess.run(cmd, check=True, timeout=600)
            print(f"\n✓ {compiler_type} 编译器安装成功!")
            return True
        except Exception as e:
            print(f"✗ 安装失败: {e}")
            return False
    
    def _install_compiler_windows(self, compiler_type):
        """在Windows上安装编译器"""
        print("\nWindows平台编译器安装:")
        print("1. MinGW-w64 (推荐)")
        print("2. Visual Studio Build Tools")
        print("3. Cygwin")
        print("4. 手动安装")
        
        choice = input("\n请选择安装方式 (1-4): ").strip()
        
        if choice == '1':
            print("\n正在下载MinGW-w64安装程序...")
            mingw_url = "https://github.com/niXman/mingw-builds-binaries/releases/download/13.2.0-rt_v11-rev1/x86_64-13.2.0-release-posix-seh-ucrt-rt_v11-rev1.7z"
            print(f"请从以下地址下载: {mingw_url}")
            print("下载后解压并添加到PATH环境变量")
            return False
        elif choice == '2':
            print("\n正在下载Visual Studio Build Tools...")
            vs_url = "https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2022"
            print(f"请访问: {vs_url}")
            print("下载并安装Visual Studio Build Tools，选择C++开发工具")
            return False
        elif choice == '3':
            print("\n正在下载Cygwin...")
            cygwin_url = "https://www.cygwin.com/setup-x86_64.exe"
            print(f"请从以下地址下载: {cygwin_url}")
            print("安装时选择gcc, g++, make包")
            return False
        else:
            print("已取消安装")
            return False
    
    def setup_environment(self):
        """设置编译环境"""
        self.compiler = self.config['compiler']
        self.lib_suffix = self.config['lib_suffix']
        self.output_dir = self.config['output_dir']
        
        # 检查多目标编译设置
        multi_config = self.config.get('multi_target', {})
        self.multi_target_enabled = multi_config.get('enabled', False)
        self.targets = multi_config.get('targets', [])
        
        if self.multi_target_enabled and self.targets:
            print(f"✓ 多目标编译模式已启用")
            print(f"✓ 目标数量: {len(self.targets)} 个")
            for i, target in enumerate(self.targets, 1):
                print(f"   [{i}] {target['system']}/{target['arch']}")
        
        # 检查交叉编译设置
        cross_config = self.config['cross_compile']
        self.cross_compile_enabled = cross_config.get('enabled', False)
        
        if self.cross_compile_enabled and not self.multi_target_enabled:
            self.target_arch = cross_config.get('target_arch', '')
            self.target_system = cross_config.get('target_system', '')
            toolchain_prefix = cross_config.get('toolchain_prefix', '')
            
            if toolchain_prefix:
                self.compiler = f"{toolchain_prefix}gcc"
                print(f"✓ 使用交叉编译器: {self.compiler}")
                print(f"✓ 目标系统: {self.target_system}")
                print(f"✓ 目标架构: {self.target_arch}")
                
                if not self.check_compiler():
                    print(f"✗ 交叉编译器 {self.compiler} 未找到")
                    install = input("是否尝试自动安装交叉编译器？(y/N): ").strip().lower()
                    if install == 'y':
                        if self.install_compiler('gcc', self.target_arch, self.target_system):
                            print("✓ 交叉编译器安装成功，请重新运行工具")
                        else:
                            print("✗ 交叉编译器安装失败")
                            sys.exit(1)
                    else:
                        print("请手动安装交叉编译器后重试")
                        sys.exit(1)
        else:
            if not self.check_compiler():
                print("⚠ 未找到gcc编译器")
                install = input("是否尝试自动安装gcc编译器？(y/N): ").strip().lower()
                if install == 'y':
                    if self.install_compiler('gcc'):
                        print("✓ gcc安装成功，请重新运行工具")
                        sys.exit(0)
                    else:
                        print("✗ gcc安装失败")
                        print("尝试使用clang...")
                        self.compiler = 'clang'
                        if not self.check_compiler():
                            install_clang = input("是否尝试自动安装clang？(y/N): ").strip().lower()
                            if install_clang == 'y':
                                if self.install_compiler('clang'):
                                    print("✓ clang安装成功，请重新运行工具")
                                    sys.exit(0)
                                else:
                                    print("✗ 没有可用的编译器!")
                                    sys.exit(1)
                        else:
                            print(f"✓ 使用编译器: {self.compiler}")
                else:
                    print("请安装gcc或clang后重试")
                    sys.exit(1)
            else:
                print(f"✓ 使用编译器: {self.compiler}")
        
        Path(self.output_dir).mkdir(exist_ok=True)
    
    def check_compiler(self):
        """检查编译器是否可用"""
        return self.check_compiler_with_name(self.compiler)
    
    def check_compiler_with_name(self, compiler_name):
        """检查指定名称的编译器是否可用"""
        try:
            result = subprocess.run([compiler_name, '--version'], 
                                  capture_output=True, text=True, timeout=2)
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
    
    def get_compile_flags(self, is_cpp=False, target_config=None):
        """获取编译标志"""
        flags = self.config['compile_flags'].copy()
        
        if is_cpp:
            flags.extend(self.config['cpp_flags'])
        else:
            flags.extend(self.config['c_flags'])
        
        if target_config:
            # 使用目标特定的编译标志
            sysroot = target_config.get('sysroot', '')
            if sysroot:
                flags.append(f'--sysroot={sysroot}')
            
            if is_cpp:
                flags.extend(target_config.get('cpp_flags', []))
            else:
                flags.extend(target_config.get('c_flags', []))
            
            flags.extend(target_config.get('link_flags', []))
        
        for include in self.config['extra_includes']:
            flags.append(f'-I{include}')
        
        for lib in self.config['extra_libs']:
            flags.append(f'-l{lib}')
        
        return flags
    
    def get_output_name(self, target_system, target_arch):
        """获取输出文件名（仅保留架构）"""
        # 架构标准化映射
        arch_map = {
            "x86_64": "x64",
            "amd64": "x64",
            "x64": "x64",
            "i386": "x86",
            "i686": "x86",
            "x86": "x86",
            "aarch64": "arm64",
            "arm64": "arm64",
            "arm": "arm",
            "armv7": "arm",
            "armv6": "arm",
            "armv5": "arm",
            "riscv64": "riscv64",
            "riscv32": "riscv32",
            "mips64": "mips64",
            "mips": "mips",
            "mipsel": "mipsel",
            "ppc64": "ppc64",
            "ppc": "ppc",
            "loongarch64": "loongarch64",
            "s390x": "s390x"
        }
        arch = arch_map.get(target_arch.lower(), target_arch.lower())
    
        # 按系统返回对应后缀
        if target_system == "windows":
            return f"{arch}.dll"
        elif target_system == "darwin":
            return f"{arch}.dylib"
        else:
            return f"{arch}.so"

    
    def scan_directory(self):
        """扫描当前目录"""
        print("\n" + "=" * 60)
        print("扫描当前目录...")
        if self.multi_target_enabled:
            print(f"多目标编译模式 - 目标数量: {len(self.targets)}")
            for i, target in enumerate(self.targets, 1):
                print(f"  [{i}] {target['system']}/{target['arch']}")
        elif self.cross_compile_enabled:
            print(f"交叉编译模式 - 目标系统: {self.target_system} | 目标架构: {self.target_arch}")
        print("=" * 60)
        
        single_files = []
        lib_projects = []
        
        for ext in ['.c', '.cpp', '.cc', '.cxx']:
            for file in Path('.').glob(f'*{ext}'):
                if file.parent == Path('.'):
                    if not file.stem.endswith('_test'):
                        single_files.append(file)
        
        for item in Path('.').iterdir():
            if item.is_dir() and item.name.endswith(self.lib_suffix):
                source_files = []
                for ext in ['.c', '.cpp', '.cc', '.cxx']:
                    source_files.extend(item.glob(f'*{ext}'))
                
                source_files = [f for f in source_files if not f.stem.endswith('_test')]
                
                if source_files:
                    lib_projects.append({
                        'name': item.name,
                        'path': item,
                        'source_files': sorted(source_files)
                    })
        
        return single_files, lib_projects
    
    def display_scan_results(self, single_files, lib_projects):
        """显示扫描结果"""
        print("\n扫描结果:")
        print("-" * 40)
        
        if single_files:
            print(f"\n单个源文件 ({len(single_files)} 个):")
            for i, file in enumerate(single_files, 1):
                size = file.stat().st_size
                print(f"  [{i:2d}] {file.name:30} ({size:,} bytes)")
        
        if lib_projects:
            print(f"\n库项目 ({len(lib_projects)} 个):")
            for i, lib in enumerate(lib_projects, 1):
                print(f"  [{i:2d}] {lib['name']}/")
                for src in lib['source_files'][:3]:
                    print(f"       {src.name}")
                if len(lib['source_files']) > 3:
                    print(f"       ... 共 {len(lib['source_files'])} 个文件")
        
        if not single_files and not lib_projects:
            print("未找到可编译的文件")
            print("支持的扩展名: .c, .cpp, .cc, .cxx")
            print(f"库项目后缀: {self.lib_suffix}")
        
        print("-" * 40)
        return len(single_files) + len(lib_projects) > 0
    
    def configure_multi_target(self):
        """多目标交叉编译配置向导 - 支持多选"""
        print("\n" + "=" * 60)
        print("多目标交叉编译配置向导")
        print("=" * 60)
        print("\n本向导将帮助您配置多个目标平台的交叉编译环境")
        print("您可以一次性选择多个目标系统和架构")
        print("-" * 60)
        
        self.targets = []
        
        # 步骤1: 选择目标系统（多选）
        print("\n[步骤1] 选择目标操作系统 (支持多选，用逗号分隔):")
        system_options = {
            '1': {'name': 'Linux', 'value': 'linux', 'desc': 'Linux系统 (Ubuntu, Debian, CentOS等)'},
            '2': {'name': 'Windows', 'value': 'windows', 'desc': 'Windows系统 (需要MinGW)'},
            '3': {'name': 'macOS', 'value': 'darwin', 'desc': 'macOS系统 (需要osxcross)'},
            '4': {'name': 'Android', 'value': 'android', 'desc': 'Android系统 (需要NDK)'},
            '5': {'name': 'FreeBSD', 'value': 'freebsd', 'desc': 'FreeBSD系统'},
            '6': {'name': 'Raspbian', 'value': 'raspbian', 'desc': '树莓派系统'},
            '7': {'name': 'OpenWrt', 'value': 'openwrt', 'desc': 'OpenWrt/LEDE系统'},
            '8': {'name': 'Alpine Linux', 'value': 'alpine', 'desc': 'Alpine Linux (musl libc)'},
            '9': {'name': 'NetBSD', 'value': 'netbsd', 'desc': 'NetBSD系统'},
            '10': {'name': 'OpenBSD', 'value': 'openbsd', 'desc': 'OpenBSD系统'},
            '11': {'name': 'DragonFly BSD', 'value': 'dragonfly', 'desc': 'DragonFly BSD系统'},
            '12': {'name': 'Solaris', 'value': 'solaris', 'desc': 'Solaris系统'},
            '13': {'name': 'QNX', 'value': 'qnx', 'desc': 'QNX实时操作系统'},
            '14': {'name': 'VxWorks', 'value': 'vxworks', 'desc': 'VxWorks实时操作系统'},
            '15': {'name': '其他', 'value': 'other', 'desc': '自定义配置'}
        }
        
        for key, option in system_options.items():
            print(f"  [{key:2}] {option['name']:12} - {option['desc']}")
        
        print("\n示例: 1,3,5  (选择Linux, macOS, FreeBSD)")
        
        while True:
            system_choices = input("\n请输入目标系统编号 (用逗号分隔): ").strip()
            if system_choices:
                selected_systems = []
                for part in system_choices.split(','):
                    part = part.strip()
                    if part in system_options:
                        if part == '15':
                            custom_system = input("请输入自定义系统名称: ").strip().lower()
                            selected_systems.append({
                                'id': part,
                                'name': custom_system,
                                'value': custom_system,
                                'desc': '自定义'
                            })
                        else:
                            selected_systems.append(system_options[part])
                
                if selected_systems:
                    break
                else:
                    print("无效选择，请重试")
            else:
                print("请至少选择一个目标系统")
        
        # 步骤2: 为每个选中的系统选择架构
        print(f"\n[步骤2] 为选中的系统选择目标架构")
        print("-" * 40)
        
        all_targets = []
        
        for sys_option in selected_systems:
            system_name = sys_option['name']
            system_value = sys_option['value']
            
            print(f"\n▶ 为 {system_name} 选择目标架构 (支持多选，用逗号分隔):")
            
            arch_options = self._get_arch_options_for_system(system_value)
            
            for key, option in arch_options.items():
                print(f"  [{key:2}] {option['name']:16} - {option['desc']}")
            
            print("  示例: 1,3,4  (选择x86, ARM, ARM64)")
            
            while True:
                arch_choices = input(f"\n请输入架构编号 (回车跳过此系统): ").strip()
                if not arch_choices:
                    print(f"  跳过 {system_name}")
                    break
                
                selected_archs = []
                for part in arch_choices.split(','):
                    part = part.strip()
                    if part in arch_options:
                        if part == '99':  # 自定义
                            custom_arch = input("请输入自定义架构名称: ").strip().lower()
                            selected_archs.append({
                                'arch': custom_arch,
                                'flags': []
                            })
                        else:
                            selected_archs.append({
                                'arch': arch_options[part]['value'],
                                'flags': arch_options[part].get('flags', [])
                            })
                
                if selected_archs:
                    break
                else:
                    print("无效选择，请重试")
            
            # 为每个选中的架构创建目标配置
            for arch_info in selected_archs:
                target_config = self._generate_target_config(system_value, arch_info['arch'], arch_info['flags'])
                if target_config:
                    all_targets.append(target_config)
                    print(f"  ✓ 添加目标: {system_name}/{arch_info['arch']}")
        
        if not all_targets:
            print("\n✗ 未选择任何目标")
            return False
        
        self.targets = all_targets
        self.multi_target_enabled = True
        
        # 步骤3: 检查并安装工具链
        print(f"\n[步骤3] 检查交叉编译工具链")
        print("-" * 40)
        
        missing_toolchains = []
        for target in self.targets:
            toolchain_prefix = target.get('toolchain_prefix', '')
            if toolchain_prefix:
                compiler_to_check = f"{toolchain_prefix}gcc"
                if not self.check_compiler_with_name(compiler_to_check):
                    missing_toolchains.append(target)
                    print(f"⚠ 未找到: {compiler_to_check} (目标: {target['system']}/{target['arch']})")
                else:
                    print(f"✓ 已安装: {compiler_to_check}")
        
        if missing_toolchains:
            print(f"\n发现 {len(missing_toolchains)} 个缺失的交叉编译工具链")
            install_all = input("是否尝试自动安装所有缺失的工具链？(y/N): ").strip().lower()
            
            if install_all == 'y':
                for target in missing_toolchains:
                    print(f"\n正在安装 {target['system']}/{target['arch']} 的工具链...")
                    if self.install_compiler('gcc', target['arch'], target['system']):
                        print(f"  ✓ 安装成功")
                    else:
                        print(f"  ✗ 安装失败，请手动安装")
        
        # 步骤4: 配置sysroot（可选）
        print(f"\n[步骤4] 配置系统根目录 (sysroot)")
        print("-" * 40)
        print("sysroot包含目标系统的头文件和库文件")
        print("可以统一配置或为每个目标单独配置")
        
        sysroot_mode = input("\n选择sysroot配置模式:\n  1. 统一配置 (所有目标使用相同sysroot)\n  2. 单独配置 (为每个目标单独配置)\n  3. 跳过\n请选择 (1-3): ").strip()
        
        if sysroot_mode == '1':
            sysroot_path = input("输入统一的sysroot路径: ").strip()
            if sysroot_path:
                sysroot_path = Path(sysroot_path).expanduser()
                if not sysroot_path.exists():
                    create_dir = input(f"路径不存在，是否创建？(y/N): ").strip().lower()
                    if create_dir == 'y':
                        sysroot_path.mkdir(parents=True, exist_ok=True)
                        print(f"✓ 已创建目录: {sysroot_path}")
                    else:
                        sysroot_path = None
                
                if sysroot_path:
                    for target in self.targets:
                        target['sysroot'] = str(sysroot_path)
                    print(f"✓ 已为所有目标设置sysroot: {sysroot_path}")
        
        elif sysroot_mode == '2':
            for target in self.targets:
                print(f"\n目标: {target['system']}/{target['arch']}")
                sysroot_path = input("  输入sysroot路径 (回车跳过): ").strip()
                if sysroot_path:
                    sysroot_path = Path(sysroot_path).expanduser()
                    if not sysroot_path.exists():
                        create_dir = input(f"  路径不存在，是否创建？(y/N): ").strip().lower()
                        if create_dir == 'y':
                            sysroot_path.mkdir(parents=True, exist_ok=True)
                            print(f"  ✓ 已创建目录: {sysroot_path}")
                        else:
                            continue
                    target['sysroot'] = str(sysroot_path)
                    print(f"  ✓ 已设置sysroot")
        
        # 步骤5: 保存配置
        self.save_config()
        
        # 步骤6: 显示配置总结
        print("\n" + "=" * 60)
        print("多目标交叉编译配置完成!")
        print("=" * 60)
        print(f"目标数量: {len(self.targets)} 个")
        print("\n目标列表:")
        for i, target in enumerate(self.targets, 1):
            print(f"  [{i}] {target['system']}/{target['arch']}")
            print(f"      工具链: {target.get('toolchain_prefix', '默认')}")
            if target.get('sysroot'):
                print(f"      sysroot: {target['sysroot']}")
            if target.get('c_flags'):
                print(f"      标志: {' '.join(target['c_flags'])}")
        print("=" * 60)
        
        input("\n按回车键继续...")
        
        return True
    
    def _get_arch_options_for_system(self, system):
        """获取特定系统的架构选项"""
        if system in ['linux', 'raspbian', 'freebsd', 'android', 'openwrt', 'alpine', 
                     'netbsd', 'openbsd', 'dragonfly', 'solaris', 'qnx', 'vxworks']:
            return {
                '1': {'name': 'x86 (32位)', 'value': 'x86', 'desc': '32位兼容模式', 'flags': ['-m32']},
                '2': {'name': 'x64 (64位)', 'value': 'x64', 'desc': '64位模式', 'flags': ['-m64']},
                '3': {'name': 'ARM (32位)', 'value': 'arm', 'desc': 'ARMv7 32位', 'flags': ['-march=armv7-a', '-mfpu=neon', '-mfloat-abi=hard']},
                '4': {'name': 'ARM64 (64位)', 'value': 'arm64', 'desc': 'ARMv8 64位', 'flags': ['-march=armv8-a']},
                '5': {'name': 'ARMv5', 'value': 'armv5', 'desc': 'ARMv5 旧设备', 'flags': ['-march=armv5te']},
                '6': {'name': 'ARMv6', 'value': 'armv6', 'desc': 'ARMv6 树莓派1', 'flags': ['-march=armv6', '-mfpu=vfp']},
                '7': {'name': 'MIPS', 'value': 'mips', 'desc': 'MIPS 32位大端', 'flags': ['-march=mips32r2']},
                '8': {'name': 'MIPSEL', 'value': 'mipsel', 'desc': 'MIPS 32位小端', 'flags': ['-march=mips32r2', '-EL']},
                '9': {'name': 'MIPS64', 'value': 'mips64', 'desc': 'MIPS 64位', 'flags': ['-march=mips64r2']},
                '10': {'name': 'PowerPC', 'value': 'ppc', 'desc': 'PowerPC 32位', 'flags': ['-mcpu=powerpc']},
                '11': {'name': 'PowerPC64', 'value': 'ppc64', 'desc': 'PowerPC 64位', 'flags': ['-mcpu=powerpc64']},
                '12': {'name': 'RISC-V 32', 'value': 'riscv32', 'desc': 'RISC-V 32位', 'flags': ['-march=rv32gc']},
                '13': {'name': 'RISC-V 64', 'value': 'riscv64', 'desc': 'RISC-V 64位', 'flags': ['-march=rv64gc']},
                '14': {'name': 'LoongArch', 'value': 'loongarch64', 'desc': '龙芯架构', 'flags': ['-march=loongarch64']},
                '15': {'name': 's390x', 'value': 's390x', 'desc': 'IBM Z架构', 'flags': ['-march=zEC12']},
                '16': {'name': 'HPPA', 'value': 'hppa', 'desc': 'HP PA-RISC', 'flags': []},
                '17': {'name': 'SPARC', 'value': 'sparc', 'desc': 'SPARC 32位', 'flags': []},
                '18': {'name': 'SPARC64', 'value': 'sparc64', 'desc': 'SPARC 64位', 'flags': []},
                '19': {'name': 'Alpha', 'value': 'alpha', 'desc': 'DEC Alpha', 'flags': []},
                '20': {'name': 'IA64', 'value': 'ia64', 'desc': 'Intel Itanium', 'flags': []},
                '99': {'name': '其他', 'value': 'other', 'desc': '自定义架构', 'flags': []}
            }
        elif system == 'windows':
            return {
                '1': {'name': 'x86 (32位)', 'value': 'x86', 'desc': 'Windows 32位', 'flags': ['-m32']},
                '2': {'name': 'x64 (64位)', 'value': 'x64', 'desc': 'Windows 64位', 'flags': ['-m64']},
                '3': {'name': 'ARM', 'value': 'arm', 'desc': 'Windows ARM 32位', 'flags': []},
                '4': {'name': 'ARM64', 'value': 'arm64', 'desc': 'Windows ARM 64位', 'flags': []},
                '5': {'name': 'ARM64EC', 'value': 'arm64ec', 'desc': 'Windows ARM64EC', 'flags': []},
                '99': {'name': '其他', 'value': 'other', 'desc': '自定义架构', 'flags': []}
            }
        elif system == 'darwin':
            return {
                '1': {'name': 'x64 (64位)', 'value': 'x64', 'desc': 'Intel Mac', 'flags': ['-arch', 'x86_64']},
                '2': {'name': 'ARM64', 'value': 'arm64', 'desc': 'Apple Silicon', 'flags': ['-arch', 'arm64']},
                '3': {'name': '通用二进制', 'value': 'universal', 'desc': '同时支持Intel和Apple Silicon', 'flags': ['-arch', 'x86_64', '-arch', 'arm64']},
                '99': {'name': '其他', 'value': 'other', 'desc': '自定义架构', 'flags': []}
            }
        else:
            return {
                '1': {'name': 'x86', 'value': 'x86', 'desc': '32位', 'flags': []},
                '2': {'name': 'x64', 'value': 'x64', 'desc': '64位', 'flags': []},
                '3': {'name': 'ARM', 'value': 'arm', 'desc': 'ARM 32位', 'flags': []},
                '4': {'name': 'ARM64', 'value': 'arm64', 'desc': 'ARM 64位', 'flags': []},
                '99': {'name': '其他', 'value': 'other', 'desc': '自定义架构', 'flags': []}
            }
    
    def _generate_target_config(self, system, arch, flags=None):
        """生成目标配置"""
        if flags is None:
            flags = []
        
        target_config = {
            'system': system,
            'arch': arch,
            'c_flags': flags.copy(),
            'cpp_flags': flags.copy(),
            'link_flags': [],
            'sysroot': '',
            'enabled': True
        }
        
        # 设置工具链前缀
        if system == 'linux':
            if arch == 'arm':
                target_config['toolchain_prefix'] = 'arm-linux-gnueabihf-'
            elif arch == 'arm64':
                target_config['toolchain_prefix'] = 'aarch64-linux-gnu-'
            elif arch == 'armv5':
                target_config['toolchain_prefix'] = 'arm-linux-gnueabi-'
            elif arch == 'armv6':
                target_config['toolchain_prefix'] = 'arm-linux-gnueabihf-'
            elif arch == 'x86':
                target_config['toolchain_prefix'] = 'i686-linux-gnu-'
            elif arch == 'x64':
                target_config['toolchain_prefix'] = 'x86_64-linux-gnu-'
            elif arch == 'mips':
                target_config['toolchain_prefix'] = 'mips-linux-gnu-'
            elif arch == 'mipsel':
                target_config['toolchain_prefix'] = 'mipsel-linux-gnu-'
            elif arch == 'mips64':
                target_config['toolchain_prefix'] = 'mips64-linux-gnuabi64-'
            elif arch == 'ppc':
                target_config['toolchain_prefix'] = 'powerpc-linux-gnu-'
            elif arch == 'ppc64':
                target_config['toolchain_prefix'] = 'powerpc64-linux-gnu-'
            elif arch == 'riscv32':
                target_config['toolchain_prefix'] = 'riscv32-linux-gnu-'
            elif arch == 'riscv64':
                target_config['toolchain_prefix'] = 'riscv64-linux-gnu-'
            elif arch == 'loongarch64':
                target_config['toolchain_prefix'] = 'loongarch64-linux-gnu-'
            elif arch == 's390x':
                target_config['toolchain_prefix'] = 's390x-linux-gnu-'
            else:
                target_config['toolchain_prefix'] = f'{arch}-linux-gnu-'
                
        elif system == 'windows':
            if arch == 'x86':
                target_config['toolchain_prefix'] = 'i686-w64-mingw32-'
            elif arch == 'x64':
                target_config['toolchain_prefix'] = 'x86_64-w64-mingw32-'
            elif arch == 'arm':
                target_config['toolchain_prefix'] = 'arm-w64-mingw32-'
            elif arch == 'arm64':
                target_config['toolchain_prefix'] = 'aarch64-w64-mingw32-'
            else:
                target_config['toolchain_prefix'] = f'{arch}-w64-mingw32-'
                
        elif system == 'darwin':
            target_config['toolchain_prefix'] = 'osxcross-'
            
        elif system == 'android':
            target_config['toolchain_prefix'] = f'{arch}-linux-android-'
            
        elif system == 'raspbian':
            target_config['toolchain_prefix'] = 'arm-linux-gnueabihf-'
            
        elif system == 'openwrt':
            target_config['toolchain_prefix'] = f'{arch}-openwrt-linux-'
            
        elif system == 'alpine':
            target_config['toolchain_prefix'] = f'{arch}-alpine-linux-musl-'
            
        elif system == 'freebsd':
            target_config['toolchain_prefix'] = f'{arch}-freebsd-'
            
        elif system == 'netbsd':
            target_config['toolchain_prefix'] = f'{arch}-netbsd-'
            
        elif system == 'openbsd':
            target_config['toolchain_prefix'] = f'{arch}-openbsd-'
            
        elif system == 'dragonfly':
            target_config['toolchain_prefix'] = f'{arch}-dragonfly-'
            
        elif system == 'solaris':
            target_config['toolchain_prefix'] = f'{arch}-solaris-'
            
        elif system == 'qnx':
            target_config['toolchain_prefix'] = f'{arch}-qnx-'
            
        elif system == 'vxworks':
            target_config['toolchain_prefix'] = f'{arch}-vxworks-'
            
        else:
            target_config['toolchain_prefix'] = f'{arch}-{system}-'
        
        return target_config
    
    def compile_for_target(self, source_item, target_config, is_lib=False, lib_info=None, verbose=False):
        """为特定目标编译"""
        target_system = target_config['system']
        target_arch = target_config['arch']
        toolchain_prefix = target_config.get('toolchain_prefix', '')
        compiler = f"{toolchain_prefix}gcc" if toolchain_prefix else 'gcc'
    
        if verbose:
            print(f"\n  编译目标: {target_system}/{target_arch}")
            print(f"  编译器: {compiler}")
    
        if is_lib:
            # 编译库项目
            lib_name = lib_info['name']
            source_files = lib_info['source_files']
    
            output_dir_name = lib_name.replace(self.lib_suffix, '')
            # 目录仍保留 system_arch，避免冲突
            output_dir = Path(self.output_dir) / output_dir_name / f"{target_system}_{target_arch}"
            output_dir.mkdir(parents=True, exist_ok=True)
    
            # 只改这里：输出文件名
            output_name = self.get_output_name(target_system, target_arch)
            output_path = output_dir / output_name
    
            has_cpp = any(f.suffix in ['.cpp', '.cc', '.cxx'] for f in source_files)
            compile_flags = self.get_compile_flags(has_cpp, target_config)
    
            cmd = [compiler] + compile_flags + ['-o', str(output_path)]
            for src in source_files:
                cmd.append(str(src))
    
            if verbose:
                print(f"  输出: {output_dir.name}/{output_name}")
    
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    
                if result.returncode == 0 and output_path.exists():
                    size = output_path.stat().st_size
    
                    # 复制源文件和头文件
                    src_dir = output_dir / 'src'
                    src_dir.mkdir(exist_ok=True)
                    for src in source_files:
                        shutil.copy2(src, src_dir / src.name)
    
                    header_dir = output_dir / 'include'
                    header_dir.mkdir(exist_ok=True)
                    for header_ext in ['.h', '.hpp']:
                        for header in lib_info['path'].glob(f'*{header_ext}'):
                            shutil.copy2(header, header_dir / header.name)
    
                    if verbose:
                        print(f"  ✓ 成功 ({size:,} bytes)")
    
                    return True, output_path
                else:
                    if verbose:
                        error_msg = result.stderr.strip().split('\n')[0] if result.stderr else "未知错误"
                        print(f"  ✗ 失败: {error_msg[:80]}")
                    return False, None
    
            except Exception as e:
                if verbose:
                    print(f"  ✗ 异常: {str(e)[:80]}")
                return False, None
    
        else:
            # 编译单个文件
            file_name = source_item.name
            file_stem = source_item.stem
            is_cpp = source_item.suffix in ['.cpp', '.cc', '.cxx']
    
            output_dir = Path(self.output_dir) / file_stem / f"{target_system}_{target_arch}"
            output_dir.mkdir(parents=True, exist_ok=True)
    
            # 只改这里：输出文件名
            output_name = self.get_output_name(target_system, target_arch)
            output_path = output_dir / output_name
    
            compile_flags = self.get_compile_flags(is_cpp, target_config)
            cmd = [compiler] + compile_flags + ['-o', str(output_path), str(source_item)]
    
            if verbose:
                print(f"  输出: {output_dir.name}/{output_name}")
    
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    
                if result.returncode == 0 and output_path.exists():
                    size = output_path.stat().st_size
                    if verbose:
                        print(f"  ✓ 成功 ({size:,} bytes)")
                    return True, output_path
                else:
                    if verbose:
                        error_msg = result.stderr.strip().split('\n')[0] if result.stderr else "未知错误"
                        print(f"  ✗ 失败: {error_msg[:80]}")
                    return False, None
    
            except Exception as e:
                if verbose:
                    print(f"  ✗ 异常: {str(e)[:80]}")
                return False, None

    
    def compile_all_multi_target(self, single_files, lib_projects, verbose=True):
        """多目标编译所有项目"""
        if not self.targets:
            print("✗ 没有配置任何目标")
            return 0, []
        
        total_targets = len(self.targets)
        total_projects = len(single_files) + len(lib_projects)
        
        if verbose:
            print("\n" + "=" * 60)
            print("开始多目标编译...")
            print(f"目标平台: {total_targets} 个")
            print(f"编译项目: {total_projects} 个")
            print("=" * 60)
        
        success_count = 0
        compiled_files = []
        
        for target_idx, target_config in enumerate(self.targets, 1):
            target_system = target_config['system']
            target_arch = target_config['arch']
            
            if verbose:
                print(f"\n[{target_idx}/{total_targets}] 目标: {target_system}/{target_arch}")
                print("-" * 40)
            
            # 编译单个文件
            for file_idx, c_file in enumerate(single_files, 1):
                if verbose:
                    print(f"\n  [{file_idx}/{len(single_files)}] 文件: {c_file.name}")
                
                success, output_file = self.compile_for_target(c_file, target_config, False, None, verbose)
                if success:
                    success_count += 1
                    compiled_files.append(output_file)
            
            # 编译库项目
            for lib_idx, lib in enumerate(lib_projects, 1):
                if verbose:
                    print(f"\n  [{lib_idx}/{len(lib_projects)}] 库: {lib['name']}/")
                
                success, output_file = self.compile_for_target(None, target_config, True, lib, verbose)
                if success:
                    success_count += 1
                    compiled_files.append(output_file)
        
        if verbose:
            print("\n" + "=" * 60)
            print("多目标编译完成!")
            print(f"总目标: {total_targets}")
            print(f"总项目: {total_projects}")
            print(f"成功编译: {success_count} 个")
            print("=" * 60)
        
        return success_count, compiled_files
    
    def compile_single_file(self, c_file, verbose=False):
        """编译单个源文件（单目标）"""
        if self.multi_target_enabled and self.targets:
            return self.compile_all_multi_target([c_file], [], verbose)
        else:
            file_name = c_file.name
            file_stem = c_file.stem
            is_cpp = c_file.suffix in ['.cpp', '.cc', '.cxx']
    
            if verbose:
                print(f"\n编译单个文件: {file_name}")
    
            target_config = None
            if self.cross_compile_enabled:
                cross = self.config.get('cross_compile', {})
                target_config = {
                    'system': self.target_system,
                    'arch': self.target_arch,
                    'c_flags': cross.get('c_flags', []),
                    'cpp_flags': cross.get('cpp_flags', []),
                    'link_flags': cross.get('link_flags', []),
                    'sysroot': cross.get('sysroot', ''),
                    'toolchain_prefix': cross.get('toolchain_prefix', '')
                }
    
            output_dir = Path(self.output_dir) / file_stem
            output_dir.mkdir(parents=True, exist_ok=True)
    
            # 关键修改：用新函数生成纯架构名
            output_name = self.get_output_name(self.get_output_system(), self.get_output_arch())
            output_path = output_dir / output_name
    
            compile_flags = self.get_compile_flags(is_cpp, target_config)
    
            cmd = [self.compiler] + compile_flags + ['-o', str(output_path), str(c_file)]
    
            if verbose:
                print(f"  输出: {output_dir.name}/{output_name}")
                print(f"  命令: {' '.join(cmd[:5])}... {' '.join(cmd[-5:])}")
    
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    
                if result.returncode == 0:
                    if output_path.exists():
                        size = output_path.stat().st_size
    
                        self.compile_history.append({
                            'file': file_name,
                            'type': 'single',
                            'output': str(output_path),
                            'size': size,
                            'time': datetime.now().isoformat(),
                            'success': True,
                            'cross_compile': self.cross_compile_enabled,
                            'target_system': self.get_output_system(),
                            'target_arch': self.get_output_arch()
                        })
    
                        if verbose:
                            print(f"  ✓ 成功 ({size:,} bytes)")
                            if self.cross_compile_enabled:
                                print(f"  目标: {self.target_system} {self.target_arch}")
                        return True, output_path
                    else:
                        if verbose:
                            print(f"  ✗ 编译成功但未生成文件")
                        return False, None
                else:
                    error_msg = result.stderr.strip().split('\n')[0] if result.stderr else "未知错误"
                    if verbose:
                        print(f"  ✗ 失败: {error_msg[:80]}")
    
                    self.compile_history.append({
                        'file': file_name,
                        'type': 'single',
                        'error': error_msg,
                        'time': datetime.now().isoformat(),
                        'success': False,
                        'cross_compile': self.cross_compile_enabled
                    })
    
                    return False, None
    
            except subprocess.TimeoutExpired:
                error_msg = "编译超时"
                if verbose:
                    print(f"  ✗ {error_msg}")
    
                self.compile_history.append({
                    'file': file_name,
                    'type': 'single',
                    'error': error_msg,
                    'time': datetime.now().isoformat(),
                    'success': False,
                    'cross_compile': self.cross_compile_enabled
                })
    
                return False, None
            except Exception as e:
                error_msg = str(e)
                if verbose:
                    print(f"  ✗ 异常: {error_msg[:80]}")
    
                self.compile_history.append({
                    'file': file_name,
                    'type': 'single',
                    'error': error_msg,
                    'time': datetime.now().isoformat(),
                    'success': False,
                    'cross_compile': self.cross_compile_enabled
                })
    
                return False, None

    
    def compile_lib_project(self, lib_info, verbose=False):
        """编译库项目（单目标）"""
        if self.multi_target_enabled and self.targets:
            return self.compile_all_multi_target([], [lib_info], verbose)
        else:
            lib_name = lib_info['name']
            source_files = lib_info['source_files']
    
            if verbose:
                print(f"\n编译库项目: {lib_name}/")
                print(f"  源文件 ({len(source_files)} 个):")
                for src in source_files[:3]:
                    print(f"    • {src.name}")
                if len(source_files) > 3:
                    print(f"    ... 共 {len(source_files)} 个文件")
    
            target_config = None
            if self.cross_compile_enabled:
                cross = self.config.get('cross_compile', {})
                target_config = {
                    'system': self.target_system,
                    'arch': self.target_arch,
                    'c_flags': cross.get('c_flags', []),
                    'cpp_flags': cross.get('cpp_flags', []),
                    'link_flags': cross.get('link_flags', []),
                    'sysroot': cross.get('sysroot', ''),
                    'toolchain_prefix': cross.get('toolchain_prefix', '')
                }
    
            output_dir_name = lib_name.replace(self.lib_suffix, '')
            output_dir = Path(self.output_dir) / output_dir_name
            output_dir.mkdir(parents=True, exist_ok=True)
    
            # 关键修改：纯架构名
            output_name = self.get_output_name(self.get_output_system(), self.get_output_arch())
            output_path = output_dir / output_name
    
            has_cpp = any(f.suffix in ['.cpp', '.cc', '.cxx'] for f in source_files)
            compile_flags = self.get_compile_flags(has_cpp, target_config)
    
            cmd = [self.compiler] + compile_flags + ['-o', str(output_path)]
            for src in source_files:
                cmd.append(str(src))
    
            if verbose:
                print(f"  输出: {output_dir_name}/{output_name}")
                print(f"  命令: {' '.join(cmd[:5])}... +{len(source_files)}个源文件")
                if self.cross_compile_enabled:
                    print(f"  目标: {self.target_system} {self.target_arch}")
    
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    
                if result.returncode == 0:
                    if output_path.exists():
                        size = output_path.stat().st_size
    
                        src_dir = output_dir / 'src'
                        src_dir.mkdir(exist_ok=True)
                        for src in source_files:
                            shutil.copy2(src, src_dir / src.name)
    
                        header_dir = output_dir / 'include'
                        header_dir.mkdir(exist_ok=True)
    
                        for header_ext in ['.h', '.hpp']:
                            for header in lib_info['path'].glob(f'*{header_ext}'):
                                shutil.copy2(header, header_dir / header.name)
    
                        self.compile_history.append({
                            'file': lib_name,
                            'type': 'library',
                            'output': str(output_path),
                            'size': size,
                            'file_count': len(source_files),
                            'time': datetime.now().isoformat(),
                            'success': True,
                            'cross_compile': self.cross_compile_enabled,
                            'target_system': self.get_output_system(),
                            'target_arch': self.get_output_arch()
                        })
    
                        if verbose:
                            print(f"  ✓ 库编译成功 ({size:,} bytes)")
                            print(f"  源文件保存在: {output_dir_name}/src/")
                            if self.cross_compile_enabled:
                                print(f"  目标: {self.target_system} {self.target_arch}")
    
                        return True, output_path
                    else:
                        if verbose:
                            print(f"  ✗ 编译成功但未生成文件")
                        return False, None
                else:
                    error_msg = result.stderr.strip().split('\n')[0] if result.stderr else "未知错误"
                    if verbose:
                        print(f"  ✗ 库编译失败: {error_msg[:80]}")
    
                    self.compile_history.append({
                        'file': lib_name,
                        'type': 'library',
                        'error': error_msg,
                        'time': datetime.now().isoformat(),
                        'success': False,
                        'cross_compile': self.cross_compile_enabled
                    })
    
                    return False, None
    
            except subprocess.TimeoutExpired:
                error_msg = "编译超时"
                if verbose:
                    print(f"  ✗ {error_msg}")
    
                self.compile_history.append({
                    'file': lib_name,
                    'type': 'library',
                    'error': error_msg,
                    'time': datetime.now().isoformat(),
                    'success': False,
                    'cross_compile': self.cross_compile_enabled
                })
    
                return False, None
            except Exception as e:
                error_msg = str(e)
                if verbose:
                    print(f"  ✗ 异常: {error_msg[:80]}")
    
                self.compile_history.append({
                    'file': lib_name,
                    'type': 'library',
                    'error': error_msg,
                    'time': datetime.now().isoformat(),
                    'success': False,
                    'cross_compile': self.cross_compile_enabled
                })
    
                return False, None

    
    def compile_all(self, single_files, lib_projects, verbose=True):
        """编译所有项目（自动判断单目标/多目标）"""
        if self.multi_target_enabled and self.targets:
            return self.compile_all_multi_target(single_files, lib_projects, verbose)
        else:
            # 原有的单目标编译代码
            total = len(single_files) + len(lib_projects)
            if total == 0:
                print("没有可编译的项目")
                return 0, []
            
            if verbose:
                print("\n" + "=" * 60)
                print("开始编译所有项目...")
                if self.cross_compile_enabled:
                    print(f"交叉编译模式 - 目标系统: {self.target_system} | 目标架构: {self.target_arch}")
                print("=" * 60)
            
            success_count = 0
            compiled_files = []
            
            if single_files:
                if verbose:
                    print(f"\n编译 {len(single_files)} 个单个源文件:")
                
                for i, c_file in enumerate(single_files, 1):
                    if verbose:
                        print(f"\n[{i}/{len(single_files)}] ", end="")
                    
                    success, output_file = self.compile_single_file(c_file, verbose)
                    if success:
                        success_count += 1
                        compiled_files.append(output_file)
            
            if lib_projects:
                if verbose:
                    print(f"\n编译 {len(lib_projects)} 个库项目:")
                
                for i, lib in enumerate(lib_projects, 1):
                    if verbose:
                        print(f"\n[{i}/{len(lib_projects)}] ", end="")
                    
                    success, output_file = self.compile_lib_project(lib, verbose)
                    if success:
                        success_count += 1
                        compiled_files.append(output_file)
            
            if verbose:
                print("\n" + "=" * 60)
                print(f"编译完成!")
                print(f"总项目: {total}")
                print(f"成功: {success_count}")
                print(f"失败: {total - success_count}")
                if self.cross_compile_enabled:
                    print(f"目标: {self.target_system} {self.target_arch}")
                print("=" * 60)
            
            return success_count, compiled_files
    
    def select_and_compile(self, single_files, lib_projects):
        """选择性编译"""
        print("\n选择性编译")
        print("=" * 60)
        if self.multi_target_enabled:
            print(f"多目标编译模式 - 目标数量: {len(self.targets)}")
            for i, target in enumerate(self.targets, 1):
                print(f"  [{i}] {target['system']}/{target['arch']}")
        elif self.cross_compile_enabled:
            print(f"交叉编译模式 - 目标系统: {self.target_system} | 目标架构: {self.target_arch}")
        
        all_items = []
        
        for i, file in enumerate(single_files, 1):
            all_items.append({
                'type': 'single',
                'name': file.name,
                'object': file,
                'index': i
            })
        
        base_index = len(single_files) + 1
        for i, lib in enumerate(lib_projects, base_index):
            all_items.append({
                'type': 'library',
                'name': lib['name'],
                'object': lib,
                'index': i
            })
        
        if not all_items:
            print("没有可编译的项目")
            return 0, []
        
        print("\n可编译的项目:")
        for item in all_items:
            if item['type'] == 'single':
                size = item['object'].stat().st_size
                print(f"  [{item['index']:2d}] 单个文件: {item['name']:25} ({size:,} bytes)")
            else:
                file_count = len(item['object']['source_files'])
                print(f"  [{item['index']:2d}] 库项目: {item['name']:25} ({file_count}个源文件)")
        
        print(f"  [ a] 编译所有项目")
        print(f"  [ s] 仅编译单个文件")
        print(f"  [ l] 仅编译库项目")
        print(f"  [ t] 选择特定目标编译")
        print(f"  [ q] 返回主菜单")
        
        while True:
            choice = input("\n请选择要编译的项目 (用逗号分隔或输入选项): ").strip().lower()
            
            if choice == 'q':
                return 0, []
            elif choice == 'a':
                return self.compile_all(single_files, lib_projects)
            elif choice == 's':
                return self.compile_all(single_files, [], verbose=True)
            elif choice == 'l':
                return self.compile_all([], lib_projects, verbose=True)
            elif choice == 't' and self.multi_target_enabled:
                return self.select_target_and_compile(single_files, lib_projects)
            else:
                selected_indices = []
                for part in choice.split(','):
                    part = part.strip()
                    if part.isdigit():
                        idx = int(part)
                        if 1 <= idx <= len(all_items):
                            selected_indices.append(idx)
                
                if selected_indices:
                    break
                else:
                    print("无效选择，请重试")
        
        selected_single_files = []
        selected_lib_projects = []
        
        for idx in selected_indices:
            item = all_items[idx - 1]
            if item['type'] == 'single':
                selected_single_files.append(item['object'])
            else:
                selected_lib_projects.append(item['object'])
        
        print(f"\n选择 {len(selected_single_files)} 个单个文件和 {len(selected_lib_projects)} 个库项目")
        
        return self.compile_all(selected_single_files, selected_lib_projects, verbose=True)
    
    def select_target_and_compile(self, single_files, lib_projects):
        """选择特定目标进行编译"""
        print("\n选择编译目标:")
        print("-" * 40)
        
        for i, target in enumerate(self.targets, 1):
            print(f"  [{i}] {target['system']}/{target['arch']}")
        
        print(f"  [a] 所有目标")
        print(f"  [q] 返回")
        
        while True:
            choice = input("\n请选择目标 (用逗号分隔): ").strip().lower()
            
            if choice == 'q':
                return 0, []
            elif choice == 'a':
                return self.compile_all_multi_target(single_files, lib_projects, True)
            else:
                selected_targets = []
                for part in choice.split(','):
                    part = part.strip()
                    if part.isdigit():
                        idx = int(part) - 1
                        if 0 <= idx < len(self.targets):
                            selected_targets.append(self.targets[idx])
                
                if selected_targets:
                    break
                else:
                    print("无效选择，请重试")
        
        # 临时设置目标列表
        original_targets = self.targets
        self.targets = selected_targets
        
        success_count, compiled_files = self.compile_all_multi_target(single_files, lib_projects, True)
        
        # 恢复原目标列表
        self.targets = original_targets
        
        return success_count, compiled_files
    
    def clean_output(self, force=False):
        """清理输出目录"""
        print("\n清理编译输出...")
        
        if not force:
            confirm = input("确认清理所有编译输出吗？(y/N): ").strip().lower()
            if confirm != 'y':
                print("取消清理")
                return
        
        removed_count = 0
        output_dir = Path(self.output_dir)
        
        if output_dir.exists():
            for item in output_dir.iterdir():
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                    removed_count += 1
                    print(f"  清理: {item.name}/")
        
        self.compile_history.clear()
        
        print(f"\n清理完成! 移除了 {removed_count} 个目录")
    
    def show_compiled_files(self):
        """显示已编译的文件"""
        print("\n已编译的库文件:")
        print("=" * 60)
        
        output_dir = Path(self.output_dir)
        if not output_dir.exists():
            print(f"输出目录不存在: {self.output_dir}")
            return
        
        lib_files = []
        total_size = 0
        target_stats = defaultdict(int)
        
        for item in output_dir.iterdir():
            if item.is_dir():
                for target_dir in item.glob('*_*'):  # 匹配 system_arch 目录
                    if target_dir.is_dir():
                        for ext in ['.so', '.dll', '.dylib']:
                            for lib in target_dir.glob(f'*{ext}'):
                                lib_files.append(lib)
                                target_name = target_dir.name
                                target_stats[target_name] += 1
        
        if not lib_files:
            print("没有找到编译的库文件")
            return
        
        lib_files.sort(key=lambda x: x.stat().st_mtime, reverse=True)
        
        print(f"找到 {len(lib_files)} 个库文件:\n")
        
        # 按目标平台分组显示
        current_target = None
        for lib in lib_files:
            size = lib.stat().st_size
            total_size += size
            mtime = datetime.fromtimestamp(lib.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
            rel_path = lib.relative_to(Path.cwd())
            
            target_name = lib.parent.name
            if target_name != current_target:
                current_target = target_name
                print(f"\n▶ {target_name}:")
            
            print(f"    {rel_path.name}")
            print(f"      大小: {size:,} bytes | 修改时间: {mtime}")
        
        print("\n" + "-" * 40)
        print(f"总大小: {total_size:,} bytes ({total_size/1024/1024:.2f} MB)")
        print(f"目标平台统计:")
        for target, count in target_stats.items():
            print(f"  {target}: {count} 个文件")
        print("=" * 60)
    
    def show_compile_history(self):
        """显示编译历史"""
        print("\n编译历史:")
        print("=" * 60)
        
        if not self.compile_history:
            print("暂无编译历史")
            return
        
        success_count = sum(1 for item in self.compile_history if item.get('success', False))
        fail_count = len(self.compile_history) - success_count
        
        print(f"总编译记录: {len(self.compile_history)}")
        print(f"成功: {success_count} | 失败: {fail_count}")
        print()
        
        for i, record in enumerate(reversed(self.compile_history), 1):
            timestamp = record.get('time', '').split('.')[0].replace('T', ' ')
            
            if record.get('success', False):
                status = "✓"
                file_type = record.get('type', 'unknown')
                output = record.get('output', '')
                size = record.get('size', 0)
                cross_compile = record.get('cross_compile', False)
                target_system = record.get('target_system', '')
                target_arch = record.get('target_arch', '')
                
                if file_type == 'single':
                    arch_info = f" [交叉编译: {target_system}/{target_arch}]" if cross_compile else ""
                    print(f"[{i}] {status} {record.get('file', '')}{arch_info}")
                    print(f"     输出: {Path(output).relative_to(Path.cwd())}")
                    print(f"     大小: {size:,} bytes | 时间: {timestamp}")
                else:
                    file_count = record.get('file_count', 0)
                    arch_info = f" [交叉编译: {target_system}/{target_arch}]" if cross_compile else ""
                    print(f"[{i}] {status} {record.get('file', '')}{arch_info}")
                    print(f"     输出: {Path(output).relative_to(Path.cwd())}")
                    print(f"     大小: {size:,} bytes | 文件数: {file_count} | 时间: {timestamp}")
            else:
                status = "✗"
                error = record.get('error', '未知错误')
                cross_compile = record.get('cross_compile', False)
                cross_info = " [交叉编译]" if cross_compile else ""
                print(f"[{i}] {status} {record.get('file', '')}{cross_info}")
                print(f"     错误: {error[:60]}... | 时间: {timestamp}")
            
            print()
        
        print("=" * 60)
    
    def generate_python_wrapper(self, lib_name):
        """为库生成Python包装器"""
        print(f"\n为 {lib_name} 生成Python包装器...")
        
        lib_dir = Path(self.output_dir) / lib_name
        if not lib_dir.exists():
            print(f"错误: 找不到库目录 {lib_name}")
            return
        
        # 查找所有目标平台的库文件
        lib_files = []
        for target_dir in lib_dir.glob('*_*'):
            if target_dir.is_dir():
                for ext in ['.so', '.dll', '.dylib']:
                    for file in target_dir.glob(f'*{ext}'):
                        lib_files.append(file)
        
        if not lib_files:
            print(f"错误: 在 {lib_name} 中找不到库文件")
            return
        
        # 生成增强版Python包装器，支持多平台
        wrapper_content = f'''#!/usr/bin/env python3
"""
{lib_name}.py - {lib_name} 库的Python包装器
自动生成，支持多平台自动选择
"""

import ctypes
import os
import sys
import platform
from pathlib import Path
from typing import Optional, Dict, List

class {lib_name.title()}Lib:
    """{lib_name} 库的Python接口 - 多平台支持"""
    
    # 支持的目标平台映射
    PLATFORM_MAP = {{
        ('linux', 'x86_64'): 'linux_x64',
        ('linux', 'i386'): 'linux_x86',
        ('linux', 'i686'): 'linux_x86',
        ('linux', 'aarch64'): 'linux_arm64',
        ('linux', 'armv7l'): 'linux_arm',
        ('linux', 'arm'): 'linux_arm',
        ('windows', 'AMD64'): 'windows_x64',
        ('windows', 'x86'): 'windows_x86',
        ('windows', 'ARM64'): 'windows_arm64',
        ('darwin', 'x86_64'): 'darwin_x64',
        ('darwin', 'arm64'): 'darwin_arm64',
    }}
    
    def __init__(self, lib_path: Optional[str] = None, target: Optional[str] = None):
        """
        初始化库
        
        Args:
            lib_path: 库文件路径，如果为None则自动查找
            target: 指定目标平台，如'linux_arm64'，不指定则自动检测
        """
        if lib_path and os.path.exists(lib_path):
            self.lib_path = Path(lib_path)
        else:
            self.lib_path = self._find_library(target)
        
        if not self.lib_path or not self.lib_path.exists():
            raise FileNotFoundError(f"找不到库文件: {{lib_name}}")
        
        self.target = target or self._detect_platform()
        self.lib = self._load_library()
        self._setup_functions()
    
    def _detect_platform(self) -> str:
        """自动检测当前平台"""
        system = platform.system().lower()
        machine = platform.machine().lower()
        
        key = (system, machine)
        if key in self.PLATFORM_MAP:
            return self.PLATFORM_MAP[key]
        
        # 尝试模糊匹配
        if system == 'linux':
            if 'arm' in machine:
                return 'linux_arm64' if '64' in machine else 'linux_arm'
            elif '86' in machine:
                return 'linux_x64' if '64' in machine else 'linux_x86'
        elif system == 'windows':
            if 'arm' in machine:
                return 'windows_arm64'
            else:
                return 'windows_x64' if '64' in machine else 'windows_x86'
        elif system == 'darwin':
            return 'darwin_arm64' if 'arm' in machine else 'darwin_x64'
        
        return f"{{system}}_{{machine}}"
    
    def _find_library(self, target: Optional[str] = None) -> Optional[Path]:
        """自动查找库文件"""
        current_dir = Path.cwd()
        
        # 如果指定了目标，直接查找对应目录
        if target:
            lib_dir = current_dir / 'c' / '{lib_name}' / target
            if lib_dir.exists():
                for ext in ['.so', '.dll', '.dylib']:
                    lib_file = lib_dir / f"{{target}}{{ext}}"
                    if lib_file.exists():
                        return lib_file
        
        # 否则查找所有可能的目录
        lib_base = current_dir / 'c' / '{lib_name}'
        if lib_base.exists():
            # 优先匹配当前平台
            current_target = self._detect_platform()
            current_dir = lib_base / current_target
            if current_dir.exists():
                for ext in ['.so', '.dll', '.dylib']:
                    lib_file = current_dir / f"{{current_target}}{{ext}}"
                    if lib_file.exists():
                        return lib_file
            
            # 如果没有匹配当前平台，返回找到的第一个
            for target_dir in lib_base.iterdir():
                if target_dir.is_dir():
                    for ext in ['.so', '.dll', '.dylib']:
                        lib_file = target_dir / f"{{target_dir.name}}{{ext}}"
                        if lib_file.exists():
                            return lib_file
        
        return None
    
    def _load_library(self):
        """加载C库"""
        if platform.system() == 'Windows':
            return ctypes.WinDLL(str(self.lib_path))
        else:
            return ctypes.CDLL(str(self.lib_path))
    
    def _setup_functions(self):
        """设置C函数签名"""
        # 注意: 这里需要根据实际的C函数进行修改
        # 以下是示例函数签名
        
        # 示例: int add(int a, int b);
        if hasattr(self.lib, 'add'):
            self.lib.add.argtypes = [ctypes.c_int, ctypes.c_int]
            self.lib.add.restype = ctypes.c_int
        
        # 示例: double multiply(double a, double b);
        if hasattr(self.lib, 'multiply'):
            self.lib.multiply.argtypes = [ctypes.c_double, ctypes.c_double]
            self.lib.multiply.restype = ctypes.c_double
        
        # 示例: void greet(const char* name);
        if hasattr(self.lib, 'greet'):
            self.lib.greet.argtypes = [ctypes.c_char_p]
            self.lib.greet.restype = None
    
    def add(self, a: int, b: int) -> int:
        """整数加法 (示例)"""
        if hasattr(self.lib, 'add'):
            return self.lib.add(a, b)
        raise NotImplementedError("add函数未实现")
    
    def multiply(self, a: float, b: float) -> float:
        """浮点数乘法 (示例)"""
        if hasattr(self.lib, 'multiply'):
            return self.lib.multiply(a, b)
        raise NotImplementedError("multiply函数未实现")
    
    def greet(self, name: str):
        """打招呼 (示例)"""
        if hasattr(self.lib, 'greet'):
            self.lib.greet(name.encode('utf-8'))
        else:
            raise NotImplementedError("greet函数未实现")
    
    def list_available_targets(self) -> List[str]:
        """列出所有可用的目标平台"""
        lib_base = Path.cwd() / 'c' / '{lib_name}'
        if not lib_base.exists():
            return []
        
        targets = []
        for target_dir in lib_base.iterdir():
            if target_dir.is_dir():
                targets.append(target_dir.name)
        return targets

if __name__ == "__main__":
    try:
        lib = {lib_name.title()}Lib()
        print(f"成功加载 {{lib_name}} 库")
        print(f"当前平台: {{lib.target}}")
        print(f"可用平台: {{lib.list_available_targets()}}")
        
        # 测试函数
        # result = lib.add(10, 20)
        # print(f"10 + 20 = {{result}}")
        
    except Exception as e:
        print(f"错误: {{e}}")
'''

        wrapper_file = Path(f"{lib_name}_wrapper.py")
        with open(wrapper_file, 'w') as f:
            f.write(wrapper_content)
        
        print(f"✓ Python包装器已生成: {wrapper_file.name}")
        print(f"  支持多平台自动选择")
        print(f"  请根据实际的C函数修改包装器中的函数签名")
    
    def show_current_config(self):
        """显示当前配置"""
        print("\n" + "=" * 60)
        print("当前编译配置")
        print("=" * 60)
        
        print(f"编译器: {self.config['compiler']}")
        print(f"输出目录: {self.config['output_dir']}")
        print(f"库后缀: {self.config['lib_suffix']}")
        
        multi_config = self.config.get('multi_target', {})
        if multi_config.get('enabled', False):
            print(f"\n多目标编译模式: ✓ 启用")
            targets = multi_config.get('targets', [])
            print(f"目标数量: {len(targets)}")
            for i, target in enumerate(targets, 1):
                print(f"  [{i}] {target.get('system', '')}/{target.get('arch', '')}")
                if target.get('toolchain_prefix'):
                    print(f"      工具链: {target['toolchain_prefix']}")
                if target.get('sysroot'):
                    print(f"      sysroot: {target['sysroot']}")
        else:
            cross_config = self.config['cross_compile']
            print(f"\n交叉编译模式: {'✓ 启用' if cross_config['enabled'] else '✗ 未启用'}")
            if cross_config['enabled']:
                print(f"  目标系统: {cross_config['target_system']}")
                print(f"  目标架构: {cross_config['target_arch']}")
                print(f"  工具链前缀: {cross_config['toolchain_prefix']}")
                if cross_config['sysroot']:
                    print(f"  Sysroot: {cross_config['sysroot']}")
                if cross_config['c_flags']:
                    print(f"  C Flags: {' '.join(cross_config['c_flags'])}")
        
        print("\n常规编译标志:")
        print(f"  编译标志: {' '.join(self.config['compile_flags'])}")
        print(f"  C标志: {' '.join(self.config['c_flags'])}")
        print(f"  C++标志: {' '.join(self.config['cpp_flags'])}")
        
        if self.config['extra_includes']:
            print(f"\n额外包含路径: {', '.join(self.config['extra_includes'])}")
        if self.config['extra_libs']:
            print(f"额外库: {', '.join(self.config['extra_libs'])}")
        
        print("=" * 60)
    
    def edit_configuration(self):
        """编辑配置"""
        print("\n" + "=" * 60)
        print("编辑编译配置")
        print("=" * 60)
        
        while True:
            print("\n可编辑的配置项:")
            print("  1. 编译器设置")
            print("  2. 多目标交叉编译配置 (推荐)")
            print("  3. 单目标交叉编译配置")
            print("  4. 编译标志")
            print("  5. 额外包含路径和库")
            print("  6. 显示当前配置")
            print("  7. 保存配置")
            print("  8. 返回主菜单")
            
            choice = input("\n选择要编辑的项 (1-8): ").strip()
            
            if choice == '1':
                print(f"\n当前编译器: {self.config['compiler']}")
                new_compiler = input("输入新的编译器 (如: gcc, clang): ").strip()
                if new_compiler:
                    self.config['compiler'] = new_compiler
                    print(f"编译器已更新为: {new_compiler}")
            
            elif choice == '2':
                if self.configure_multi_target():
                    self.multi_target_enabled = True
                    self.cross_compile_enabled = False
            
            elif choice == '3':
                from copy import deepcopy
                old_cross = deepcopy(self.config['cross_compile'])
                if self._configure_single_target():
                    self.cross_compile_enabled = self.config['cross_compile']['enabled']
                    self.multi_target_enabled = False
            
            elif choice == '4':
                print("\n编辑编译标志:")
                print(f"当前编译标志: {' '.join(self.config['compile_flags'])}")
                new_flags = input("输入新的编译标志 (用空格分隔): ").strip().split()
                if new_flags:
                    self.config['compile_flags'] = new_flags
                    print(f"编译标志已更新: {' '.join(new_flags)}")
                
                print(f"\n当前C标志: {' '.join(self.config['c_flags'])}")
                new_c_flags = input("输入新的C标志 (用空格分隔): ").strip().split()
                if new_c_flags:
                    self.config['c_flags'] = new_c_flags
                    print(f"C标志已更新: {' '.join(new_c_flags)}")
                
                print(f"\n当前C++标志: {' '.join(self.config['cpp_flags'])}")
                new_cpp_flags = input("输入新的C++标志 (用空格分隔): ").strip().split()
                if new_cpp_flags:
                    self.config['cpp_flags'] = new_cpp_flags
                    print(f"C++标志已更新: {' '.join(new_cpp_flags)}")
            
            elif choice == '5':
                print("\n编辑额外包含路径 (当前: " + ', '.join(self.config['extra_includes']) + ")")
                includes_input = input("输入新的包含路径，多个用逗号分隔: ").strip()
                if includes_input:
                    includes = [inc.strip() for inc in includes_input.split(',')]
                    self.config['extra_includes'] = includes
                    print(f"包含路径已更新: {', '.join(includes)}")
                
                print("\n编辑额外库 (当前: " + ', '.join(self.config['extra_libs']) + ")")
                libs_input = input("输入新的库，多个用逗号分隔: ").strip()
                if libs_input:
                    libs = [lib.strip() for lib in libs_input.split(',')]
                    self.config['extra_libs'] = libs
                    print(f"库已更新: {', '.join(libs)}")
            
            elif choice == '6':
                self.show_current_config()
                input("\n按回车键继续...")
            
            elif choice == '7':
                config_file = input("输入配置文件名 (默认: compile_config.json): ").strip()
                if not config_file:
                    config_file = 'compile_config.json'
                self.save_config(config_file)
                print(f"配置已保存到: {config_file}")
            
            elif choice == '8':
                break
            
            else:
                print("无效选择")
    
    def _configure_single_target(self):
        """配置单目标交叉编译（保留原有功能）"""
        print("\n" + "=" * 60)
        print("单目标交叉编译配置向导")
        print("=" * 60)
        
        enable_cross = input("启用交叉编译？(y/N): ").strip().lower()
        if enable_cross != 'y':
            self.config['cross_compile']['enabled'] = False
            return False
        
        arch_options = {
            '1': {'name': 'Linux x64', 'system': 'linux', 'arch': 'x64', 'prefix': 'x86_64-linux-gnu-', 'flags': ['-m64']},
            '2': {'name': 'Linux x86', 'system': 'linux', 'arch': 'x86', 'prefix': 'i686-linux-gnu-', 'flags': ['-m32']},
            '3': {'name': 'Linux ARM (32位)', 'system': 'linux', 'arch': 'arm', 'prefix': 'arm-linux-gnueabihf-', 'flags': ['-march=armv7-a', '-mfpu=neon', '-mfloat-abi=hard']},
            '4': {'name': 'Linux ARM64', 'system': 'linux', 'arch': 'arm64', 'prefix': 'aarch64-linux-gnu-', 'flags': ['-march=armv8-a']},
            '5': {'name': 'Linux MIPS', 'system': 'linux', 'arch': 'mips', 'prefix': 'mips-linux-gnu-', 'flags': ['-march=mips32r2']},
            '6': {'name': 'Linux RISC-V 64', 'system': 'linux', 'arch': 'riscv64', 'prefix': 'riscv64-linux-gnu-', 'flags': ['-march=rv64gc']},
            '7': {'name': 'Windows x64 (MinGW)', 'system': 'windows', 'arch': 'x64', 'prefix': 'x86_64-w64-mingw32-', 'flags': ['-m64']},
            '8': {'name': 'Windows x86 (MinGW)', 'system': 'windows', 'arch': 'x86', 'prefix': 'i686-w64-mingw32-', 'flags': ['-m32']},
            '9': {'name': 'macOS x64', 'system': 'darwin', 'arch': 'x64', 'prefix': 'osxcross-', 'flags': ['-arch', 'x86_64']},
            '10': {'name': 'macOS ARM64', 'system': 'darwin', 'arch': 'arm64', 'prefix': 'osxcross-', 'flags': ['-arch', 'arm64']},
            '11': {'name': 'Android ARM64', 'system': 'android', 'arch': 'arm64', 'prefix': 'aarch64-linux-android-', 'flags': []},
            '12': {'name': '自定义', 'system': '', 'arch': '', 'prefix': '', 'flags': []}
        }
        
        print("\n请选择目标平台:")
        for key, option in arch_options.items():
            print(f"  [{key:2}] {option['name']}")
        
        while True:
            arch_choice = input("\n选择平台 (1-12): ").strip()
            if arch_choice in arch_options:
                break
            print("无效选择，请重试")
        
        selected = arch_options[arch_choice]
        
        if arch_choice == '12':
            # 自定义配置
            target_system = input("输入目标系统名称: ").strip().lower()
            target_arch = input("输入目标架构名称: ").strip().lower()
            toolchain_prefix = input("输入工具链前缀: ").strip()
            if toolchain_prefix and not toolchain_prefix.endswith('-'):
                toolchain_prefix += '-'
            c_flags = input("输入编译器标志 (用空格分隔): ").strip().split()
            
            self.config['cross_compile']['target_system'] = target_system
            self.config['cross_compile']['target_arch'] = target_arch
            self.config['cross_compile']['toolchain_prefix'] = toolchain_prefix
            self.config['cross_compile']['c_flags'] = c_flags
            self.config['cross_compile']['cpp_flags'] = c_flags.copy()
        else:
            self.config['cross_compile']['target_system'] = selected['system']
            self.config['cross_compile']['target_arch'] = selected['arch']
            self.config['cross_compile']['toolchain_prefix'] = selected['prefix']
            self.config['cross_compile']['c_flags'] = selected['flags'].copy()
            self.config['cross_compile']['cpp_flags'] = selected['flags'].copy()
        
        # 配置sysroot
        print("\n配置sysroot (系统根目录，可选)")
        sysroot = input("输入sysroot路径 (回车跳过): ").strip()
        if sysroot:
            sysroot_path = Path(sysroot).expanduser()
            if sysroot_path.exists() or input("路径不存在，是否创建？(y/N): ").strip().lower() == 'y':
                sysroot_path.mkdir(parents=True, exist_ok=True)
                self.config['cross_compile']['sysroot'] = str(sysroot_path)
                print(f"✓ Sysroot路径: {sysroot_path}")
        
        self.config['cross_compile']['enabled'] = True
        self.save_config()
        
        print("\n" + "=" * 60)
        print("单目标交叉编译配置完成!")
        print(f"目标: {self.config['cross_compile']['target_system']}/{self.config['cross_compile']['target_arch']}")
        print("=" * 60)
        
        input("\n按回车键继续...")
        return True
    
    def show_help(self):
        """显示帮助信息"""
        help_text = """
C/C++源文件编译工具 v2.0 - 帮助信息
============================================================

🎯 主要特性:
  - 支持多目标交叉编译（一次性编译到多个平台）
  - 支持 20+ 种操作系统和 30+ 种处理器架构
  - 自动检测并安装交叉编译工具链
  - 智能生成多平台Python包装器
  - 配置持久化，支持批量编译

📦 支持的操作系统:
  Linux, Windows, macOS, Android, FreeBSD, NetBSD, OpenBSD,
  DragonFly BSD, Solaris, Raspbian, OpenWrt, Alpine Linux,
  QNX, VxWorks 等

🔧 支持的处理器架构:
  x86, x64, ARM (v5/v6/v7), ARM64, MIPS (32/64), PowerPC (32/64),
  RISC-V (32/64), LoongArch, s390x, HPPA, SPARC, Alpha, IA64 等

📁 文件结构:
  project/
    ├── module1.c              # 单个源文件
    ├── module2.cpp            # 单个源文件
    ├── mylib_lib/             # 库项目（以_lib结尾）
    │   ├── file1.c
    │   ├── file2.c
    │   └── header.h
    ├── compile_config.json    # 配置文件
    └── compile.py             # 本编译工具

📂 编译后生成（多目标示例）:
  project/
    └── c/
        ├── module1/
        │   ├── linux_arm64/linux_arm64.so
        │   ├── windows_x64/windows_x64.dll
        │   └── darwin_arm64/darwin_arm64.dylib
        └── mylib/
            ├── linux_arm64/linux_arm64.so
            │   ├── src/          # 源文件副本
            │   └── include/       # 头文件副本
            ├── windows_x64/windows_x64.dll
            └── darwin_arm64/darwin_arm64.dylib

🐍 Python调用（自动选择平台）:
  from mylib_wrapper import MylibLib
  lib = MylibLib()  # 自动加载当前平台的库
  result = lib.my_function()

🚀 快速开始:
  1. 运行: python compile.py
  2. 选择"多目标交叉编译配置向导"
  3. 选择目标系统（可多选，如: 1,3,5）
  4. 为每个系统选择架构（可多选）
  5. 自动安装缺失的工具链
  6. 选择"编译所有项目"

⌨️ 快捷键:
  [数字]    - 选择菜单项
  [a]      - 编译所有
  [t]      - 选择特定目标编译
  [q]      - 退出
  [Ctrl+C] - 中断程序
============================================================
"""
        print(help_text)
    
    def main_menu(self):
        """显示主菜单"""
        self.clear_screen()
        
        print("=" * 60)
        print("C/C++源文件编译工具 v2.0 - 多目标交叉编译")
        print("=" * 60)
        print(f"本机系统: {self.system} | 本机架构: {self.arch}")
        print(f"当前编译器: {self.compiler}")
        
        if self.multi_target_enabled and self.targets:
            print(f"多目标模式: ✓ 启用 | 目标数量: {len(self.targets)}")
            for i, target in enumerate(self.targets[:3], 1):
                print(f"  ├─ {target['system']}/{target['arch']}")
            if len(self.targets) > 3:
                print(f"  └─ ... 共 {len(self.targets)} 个目标")
        elif self.cross_compile_enabled:
            print(f"交叉编译模式: ✓ 启用 | 目标: {self.target_system}/{self.target_arch}")
        else:
            print(f"本机编译模式: ✓ 启用")
        
        print(f"输出目录: {self.output_dir}")
        print(f"库后缀: {self.lib_suffix}")
        print("=" * 60)
        
        single_files, lib_projects = self.scan_directory()
        has_projects = self.display_scan_results(single_files, lib_projects)
        
        print("\n主菜单:")
        print("  1. 编译所有项目")
        print("  2. 选择性编译")
        print("  3. 仅编译单个文件")
        print("  4. 仅编译库项目")
        print("  5. 显示已编译的文件")
        print("  6. 显示编译历史")
        print("  7. 清理输出目录")
        print("  8. 生成Python包装器")
        print("  9. 编辑编译配置")
        print(" 10. 多目标交叉编译配置向导 ⭐")
        print(" 11. 安装编译器/交叉编译器")
        print(" 12. 帮助信息")
        print("  0. 重新扫描目录")
        print("  q. 退出")
        
        return single_files, lib_projects, has_projects
    
    def run_interactive(self):
        """运行交互式模式"""
        print("欢迎使用 C/C++ 源文件编译工具 v2.0!")
        print("支持多目标交叉编译和自动安装工具链")
        print("按 Ctrl+C 退出程序")
        print()
        
        while True:
            try:
                single_files, lib_projects, has_projects = self.main_menu()
                
                if not has_projects:
                    print("\n当前目录没有可编译的项目")
                    choice = input("按回车键重新扫描，或输入q退出: ").strip().lower()
                    if choice == 'q':
                        break
                    continue
                
                choice = input("\n请选择操作: ").strip().lower()
                
                if choice == '1':
                    success_count, _ = self.compile_all(single_files, lib_projects)
                    if success_count > 0:
                        input("\n按回车键返回主菜单...")
                
                elif choice == '2':
                    success_count, _ = self.select_and_compile(single_files, lib_projects)
                    if success_count > 0:
                        input("\n按回车键返回主菜单...")
                
                elif choice == '3':
                    success_count, _ = self.compile_all(single_files, [], verbose=True)
                    if success_count > 0:
                        input("\n按回车键返回主菜单...")
                
                elif choice == '4':
                    success_count, _ = self.compile_all([], lib_projects, verbose=True)
                    if success_count > 0:
                        input("\n按回车键返回主菜单...")
                
                elif choice == '5':
                    self.show_compiled_files()
                    input("\n按回车键返回主菜单...")
                
                elif choice == '6':
                    self.show_compile_history()
                    input("\n按回车键返回主菜单...")
                
                elif choice == '7':
                    self.clean_output()
                    input("\n按回车键返回主菜单...")
                
                elif choice == '8':
                    if lib_projects:
                        print("\n可生成包装器的库:")
                        for i, lib in enumerate(lib_projects, 1):
                            lib_name = lib['name'].replace(self.lib_suffix, '')
                            print(f"  [{i}] {lib_name}")
                        
                        lib_choice = input("\n选择库编号: ").strip()
                        if lib_choice.isdigit():
                            idx = int(lib_choice) - 1
                            if 0 <= idx < len(lib_projects):
                                lib_name = lib_projects[idx]['name'].replace(self.lib_suffix, '')
                                self.generate_python_wrapper(lib_name)
                    else:
                        print("没有可生成包装器的库项目")
                    input("\n按回车键返回主菜单...")
                
                elif choice == '9':
                    self.edit_configuration()
                    input("\n按回车键返回主菜单...")
                
                elif choice == '10':
                    if self.configure_multi_target():
                        self.multi_target_enabled = True
                        self.cross_compile_enabled = False
                        self.setup_environment()
                    input("\n按回车键返回主菜单...")
                
                elif choice == '11':
                    print("\n安装编译器/交叉编译器:")
                    print("  1. 安装gcc (本机)")
                    print("  2. 安装clang (本机)")
                    print("  3. 安装交叉编译工具链 (通过多目标向导)")
                    print("  4. 返回")
                    
                    install_choice = input("\n请选择: ").strip()
                    if install_choice == '1':
                        self.install_compiler('gcc')
                    elif install_choice == '2':
                        self.install_compiler('clang')
                    elif install_choice == '3':
                        self.configure_multi_target()
                    input("\n按回车键返回主菜单...")
                
                elif choice == '12':
                    self.show_help()
                    input("\n按回车键返回主菜单...")
                
                elif choice == '0':
                    continue
                
                elif choice == 'q':
                    print("\n感谢使用，再见!")
                    break
                
                else:
                    print("无效选择")
                    time.sleep(1)
                    
            except KeyboardInterrupt:
                print("\n\n程序被中断，退出...")
                break
            except Exception as e:
                print(f"\n错误: {e}")
                import traceback
                traceback.print_exc()
                input("按回车键继续...")
    
    def run_batch_mode(self, clean=False, all_files=False):
        """运行批处理模式"""
        if clean:
            self.clean_output(force=True)
            return
        
        single_files, lib_projects = self.scan_directory()
        
        if not single_files and not lib_projects:
            print("没有找到可编译的文件")
            return
        
        if all_files:
            success_count, compiled_files = self.compile_all(single_files, lib_projects, verbose=True)
            print(f"\n批处理编译完成: {success_count} 个成功")
            
            if compiled_files:
                print("\n生成的库文件:")
                for file in compiled_files:
                    print(f"  {file.relative_to(Path.cwd())}")
        else:
            success_count, compiled_files = self.compile_all(single_files, [], verbose=True)
            print(f"\n批处理编译完成: {success_count} 个成功")
    
    def clear_screen(self):
        """清屏"""
        os.system('clear' if os.name != 'nt' else 'cls')

def main():
    """主函数"""
    parser = argparse.ArgumentParser(description='C/C++源文件编译工具 v2.0 - 多目标交叉编译')
    parser.add_argument('--config', '-c', help='配置文件路径')
    parser.add_argument('--batch', '-b', action='store_true', help='批处理模式')
    parser.add_argument('--clean', action='store_true', help='清理输出目录')
    parser.add_argument('--all', '-a', action='store_true', help='编译所有文件')
    parser.add_argument('--interactive', '-i', action='store_true', help='交互式模式')
    parser.add_argument('--install-gcc', action='store_true', help='安装gcc编译器')
    parser.add_argument('--install-clang', action='store_true', help='安装clang编译器')
    parser.add_argument('--multi-target', action='store_true', help='配置多目标交叉编译')
    parser.add_argument('--list-targets', action='store_true', help='列出支持的目标平台')
    
    args = parser.parse_args()
    
    if args.list_targets:
        print("\n支持的目标平台:")
        print("=" * 60)
        print("\n操作系统:")
        systems = ['linux', 'windows', 'darwin', 'android', 'freebsd', 'netbsd', 
                  'openbsd', 'dragonfly', 'solaris', 'raspbian', 'openwrt', 
                  'alpine', 'qnx', 'vxworks']
        for s in systems:
            print(f"  - {s}")
        
        print("\n处理器架构:")
        archs = ['x86', 'x64', 'arm', 'arm64', 'armv5', 'armv6', 'mips', 'mipsel',
                'mips64', 'ppc', 'ppc64', 'riscv32', 'riscv64', 'loongarch64',
                's390x', 'hppa', 'sparc', 'sparc64', 'alpha', 'ia64']
        for a in archs:
            print(f"  - {a}")
        return 0
    
    compiler = CCompiler(args.config)
    
    if args.install_gcc:
        compiler.install_compiler('gcc')
    elif args.install_clang:
        compiler.install_compiler('clang')
    elif args.multi_target:
        compiler.configure_multi_target()
    elif args.batch or args.clean or args.all:
        compiler.run_batch_mode(clean=args.clean, all_files=args.all)
    elif args.interactive:
        compiler.run_interactive()
    else:
        compiler.run_interactive()

if __name__ == "__main__":
    main()
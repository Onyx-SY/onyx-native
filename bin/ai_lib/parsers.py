# -*- coding: utf-8 -*-
"""
Onyx AI 解析模块 — SSE 结构化响应 / AI 原始响应 / 遗留 Shell 命令解析

从 bin/ai_cmd.py 提取，零功能变更。
"""

import re
from typing import Dict, Any, List




def parse_sse_structured_response(sse_text: str) -> Dict[str, Any]:
    """
    解析服务端返回的SSE结构化文本。

    新版格式（无 @@SHELL，直接 SSE 事件）:
      [TXT]:内容行         — 服务端逐行包装的 AI 回答（去掉 [TXT]: 前缀后重组）
      [ANSWER]:yes/no      — 服务端直接给出的 answer 字段
      [ANALYSIS]:文本      — 服务端直接给出的分析
      [plan]...[plan:done] — 多行计划块
      [tool:NAME PARAMS]...[tool:NAME:done] — 工具调用块

    兼容旧版 @@SHELL 格式:
      @@SHELL
      >>>>>>>>>>
      CMD1
      >>>>>>>>>>
      [ANALYSIS]:...
      [TXT]...[TXT:DONE]
    """
    result = {
        "answer": "no",
        "ask": "",
        "analysis": "",
        "txt": "",
        "tag": "",
        "memory": "",
        "plan": "",
        "sleep": None,
        "class": "1",
        "prompt": "",
        "tool_calls": [],
    }
    commands = []

    lines = sse_text.split('\n')

    # ── 第一遍：收集 [TXT]: 行，识别直接字段 ──
    txt_raw_lines = []
    direct_fields = {}
    has_txt_wrapped = False
    plan_lines_raw = []
    in_plan = False

    i = 0
    while i < len(lines):
        stripped = lines[i].rstrip('\r').strip()
        i += 1

        if not stripped:
            if in_plan:
                plan_lines_raw.append('')
            continue

        if stripped.startswith('event: '):
            continue
        if stripped.startswith('[STATUS]:'):
            continue
        if stripped.startswith('[DEBUG]:'):
            continue

        # ── 新版 [TXT]: 逐行包装 ──
        if stripped.startswith('[TXT]:') and not stripped.startswith('[TXT:DONE]'):
            has_txt_wrapped = True
            content = stripped[6:]
            txt_raw_lines.append(content)
            continue

        # ── [TXT:DONE] 终止标记 ──
        if stripped.startswith('[TXT:DONE]'):
            remainder = stripped[10:]
            if remainder:
                lines.insert(i, remainder)
            continue

        # ── 原始 [TXT]...[TXT:DONE] 块 ──
        if stripped == '[TXT]' or (stripped.startswith('[TXT]') and not stripped.startswith('[TXT:DONE]') and not stripped.startswith('[TXT]:')):
            has_txt_wrapped = True
            if stripped != '[TXT]':
                inline_content = stripped[5:]
                done_pos = inline_content.find('[TXT:DONE]')
                if done_pos >= 0:
                    txt_part = inline_content[:done_pos]
                    remainder = inline_content[done_pos + 10:]
                    if txt_part:
                        txt_raw_lines.append(txt_part)
                    if remainder:
                        lines.insert(i, remainder)
                else:
                    if inline_content:
                        txt_raw_lines.append(inline_content)
                    while i < len(lines):
                        next_line = lines[i].rstrip('\r').strip()
                        i += 1
                        done_pos = next_line.find('[TXT:DONE]')
                        if done_pos >= 0:
                            if done_pos > 0:
                                txt_raw_lines.append(next_line[:done_pos])
                            remainder = next_line[done_pos + 10:]
                            if remainder:
                                lines.insert(i, remainder)
                            break
                        txt_raw_lines.append(next_line)
            else:
                while i < len(lines):
                    next_line = lines[i].rstrip('\r').strip()
                    i += 1
                    done_pos = next_line.find('[TXT:DONE]')
                    if done_pos >= 0:
                        if done_pos > 0:
                            txt_raw_lines.append(next_line[:done_pos])
                        remainder = next_line[done_pos + 10:]
                        if remainder:
                            lines.insert(i, remainder)
                        break
                    txt_raw_lines.append(next_line)
            continue

        # ── [PROMPT]...[PROMPT:DONE] 多行块 ──
        if stripped == '[PROMPT]':
            prompt_lines = []
            while i < len(lines):
                next_line = lines[i].rstrip('\r').strip()
                i += 1
                done_pos = next_line.find('[PROMPT:DONE]')
                if done_pos >= 0:
                    if done_pos > 0:
                        prompt_lines.append(next_line[:done_pos])
                    remainder = next_line[done_pos + 13:]
                    if remainder:
                        lines.insert(i, remainder)
                    break
                prompt_lines.append(next_line)
            direct_fields['PROMPT'] = '\n'.join(prompt_lines).strip()
            continue
        if stripped.startswith('[PROMPT]') and not stripped.startswith('[PROMPT:DONE]') and not stripped.startswith('[PROMPT]:') and '[PROMPT:DONE]' in stripped:
            inline = stripped[8:]
            done_pos = inline.find('[PROMPT:DONE]')
            if done_pos > 0:
                direct_fields['PROMPT'] = inline[:done_pos].strip()
            remainder = inline[done_pos + 13:] if done_pos >= 0 else ''
            if remainder:
                lines.insert(i, remainder)
            continue

        # ── [TAG]...[TAG:DONE] 多行块 ──
        if stripped == '[TAG]':
            tag_lines = []
            while i < len(lines):
                next_line = lines[i].rstrip('\r').strip()
                i += 1
                done_pos = next_line.find('[TAG:DONE]')
                if done_pos >= 0:
                    if done_pos > 0:
                        tag_lines.append(next_line[:done_pos])
                    remainder = next_line[done_pos + 10:]
                    if remainder:
                        lines.insert(i, remainder)
                    break
                tag_lines.append(next_line)
            direct_fields['TAG'] = '\n'.join(tag_lines).strip()
            continue
        if stripped.startswith('[TAG]') and not stripped.startswith('[TAG:DONE]') and not stripped.startswith('[TAG]:') and '[TAG:DONE]' in stripped:
            inline = stripped[4:]
            done_pos = inline.find('[TAG:DONE]')
            if done_pos > 0:
                direct_fields['TAG'] = inline[:done_pos].strip()
            remainder = inline[done_pos + 10:] if done_pos >= 0 else ''
            if remainder:
                lines.insert(i, remainder)
            continue

        # ── [ANALYSIS]...[ANALYSIS:DONE] 多行块 ──
        if stripped == '[ANALYSIS]':
            analysis_lines = []
            while i < len(lines):
                next_line = lines[i].rstrip('\r').strip()
                i += 1
                done_pos = next_line.find('[ANALYSIS:DONE]')
                if done_pos >= 0:
                    if done_pos > 0:
                        analysis_lines.append(next_line[:done_pos])
                    remainder = next_line[done_pos + 15:]
                    if remainder:
                        lines.insert(i, remainder)
                    break
                analysis_lines.append(next_line)
            direct_fields['ANALYSIS'] = '\n'.join(analysis_lines).strip()
            continue
        if stripped.startswith('[ANALYSIS]') and not stripped.startswith('[ANALYSIS:DONE]') and not stripped.startswith('[ANALYSIS]:') and '[ANALYSIS:DONE]' in stripped:
            inline = stripped[10:]
            done_pos = inline.find('[ANALYSIS:DONE]')
            if done_pos > 0:
                direct_fields['ANALYSIS'] = inline[:done_pos].strip()
            remainder = inline[done_pos + 15:] if done_pos >= 0 else ''
            if remainder:
                lines.insert(i, remainder)
            continue

        # ── [PLAN]...[PLAN:DONE] 多行块（新大写格式）──
        if stripped == '[PLAN]':
            plan_lines_new = []
            while i < len(lines):
                next_line = lines[i].rstrip('\r').strip()
                i += 1
                done_pos = next_line.find('[PLAN:DONE]')
                if done_pos >= 0:
                    if done_pos > 0:
                        plan_lines_new.append(next_line[:done_pos])
                    remainder = next_line[done_pos + 11:]
                    if remainder:
                        lines.insert(i, remainder)
                    break
                plan_lines_new.append(next_line)
            result['plan'] = '\n'.join(plan_lines_new).strip()
            continue
        if stripped.startswith('[PLAN]') and not stripped.startswith('[PLAN:DONE]') and '[PLAN:DONE]' in stripped:
            inline = stripped[6:]
            done_pos = inline.find('[PLAN:DONE]')
            if done_pos > 0:
                result['plan'] = inline[:done_pos].strip()
            remainder = inline[done_pos + 11:] if done_pos >= 0 else ''
            if remainder:
                lines.insert(i, remainder)
            continue

        # ── 独立终止标记 ──
        if stripped.startswith('[PROMPT:DONE]'):
            remainder = stripped[13:]
            if remainder:
                lines.insert(i, remainder)
            continue
        if stripped.startswith('[ANALYSIS:DONE]'):
            remainder = stripped[15:]
            if remainder:
                lines.insert(i, remainder)
            continue
        if stripped.startswith('[PLAN:DONE]'):
            remainder = stripped[11:]
            if remainder:
                lines.insert(i, remainder)
            continue
        if stripped.startswith('[TAG:DONE]'):
            remainder = stripped[10:]
            if remainder:
                lines.insert(i, remainder)
            continue


        # ── 直接字段标记 [ANSWER]: / [ANALYSIS]: / ... ──
        field_match = re.match(r'^\[(ANSWER|ANALYSIS|ASK|MEMORY|TAG|CLASS|SLEEP|PROMPT)\]:', stripped)
        if field_match:
            field_name = field_match.group(1)
            field_value = stripped[field_match.end():].strip()
            direct_fields[field_name] = field_value
            continue

        # ── 无冒号字段标记：[ANSWER]yes / [PROMPT]text ──
        field_no_colon = re.match(r'^\[(ANSWER|ANALYSIS|PROMPT|TAG|MEMORY|CLASS|SLEEP)\]\s*(.*)', stripped)
        if field_no_colon:
            field_name = field_no_colon.group(1)
            field_value = field_no_colon.group(2).strip()
            if field_value:
                direct_fields[field_name] = field_value
                continue

        # ── 多行字段（无冒号）：[FIELD]\\nvalue 格式 ──
        field_multi = re.match(r'^\[(ANALYSIS|ANSWER|TAG|MEMORY|CLASS|SLEEP|PROMPT)\]$', stripped)
        if field_multi:
            field_name = field_multi.group(1)
            value_lines = []
            while i < len(lines):
                next_stripped = lines[i].rstrip('\r').strip()
                if not next_stripped or next_stripped.startswith('['):
                    break
                value_lines.append(lines[i])
                i += 1
            direct_fields[field_name] = '\n'.join(value_lines).strip()
            continue

        # ── [plan] 多行块（小写旧格式）──
        if stripped == '[plan]':
            in_plan = True
            continue
        if in_plan:
            if stripped == '[plan:done]':
                in_plan = False
                result['plan'] = '\n'.join(plan_lines_raw).strip()
                continue
            plan_lines_raw.append(stripped)
            continue

        # ── [tool:NAME PARAMS] 多行块 ──
        tool_match = re.match(r'^\[tool:(\S+)\s*(.*)\]$', stripped)
        if tool_match:
            tool_name = tool_match.group(1)
            tool_params = tool_match.group(2).strip()
            tool_body_lines = []
            done_marker = f'[tool:{tool_name}:done]'
            while i < len(lines) and lines[i].rstrip('\r').strip() != done_marker:
                tool_body_lines.append(lines[i])
                i += 1
            result["tool_calls"].append({
                "name": tool_name,
                "params_str": tool_params,
                "body": '\n'.join(tool_body_lines).strip(),
            })
            i += 1
            continue

        # ── 旧版 @@SHELL 兼容 ──
        if stripped.startswith('@@SHELL'):
            remainder = stripped[7:]
            if remainder:
                lines.insert(i, remainder)
            legacy = _parse_legacy_shell(lines, i)
            commands.extend(legacy.get('commands', []))
            for k, v in legacy.get('fields', {}).items():
                if k not in direct_fields:
                    direct_fields[k] = v
            result["tool_calls"].extend(legacy.get('tool_calls', []))
            if legacy.get('plan'):
                result['plan'] = legacy['plan']
            break

        # ── 旧版分隔符 ──
        if stripped == '>>>>>>>>>>':
            continue

        # ── 未匹配任何已知模式的行 ──
        if stripped and not stripped.startswith('event:'):
            txt_raw_lines.append(stripped)

    # ── 第二遍：从 [TXT]: 包装内容中解析 AI 原始响应 ──
    if has_txt_wrapped and txt_raw_lines:
        raw_text = '\n'.join(txt_raw_lines)
        inner = _parse_ai_raw_response(raw_text)
        if inner.get('txt') and not result['txt']:
            result['txt'] = inner['txt']
        if not result['txt'] and raw_text.strip():
            result['txt'] = raw_text.strip()
        if inner.get('analysis') and not direct_fields.get('ANALYSIS'):
            result['analysis'] = inner['analysis']
        if inner.get('answer') and not direct_fields.get('ANSWER'):
            result['answer'] = inner['answer']
        if not direct_fields.get('ANSWER') and not result.get('answer'):
            m_ans = re.search(r'\[ANSWER\](yes|no)', raw_text)
            if m_ans:
                result['answer'] = m_ans.group(1)
        if inner.get('ask') and not direct_fields.get('ASK'):
            result['ask'] = inner['ask']
        if inner.get('tag') and not direct_fields.get('TAG'):
            result['tag'] = inner['tag']
        if inner.get('memory') and not direct_fields.get('MEMORY'):
            result['memory'] = inner['memory']
        if inner.get('plan') and not result['plan']:
            result['plan'] = inner['plan']
        if inner.get('class') and not direct_fields.get('CLASS'):
            result['class'] = inner['class']
        result["tool_calls"].extend(inner.get('tool_calls', []))

    # ── 纯文本兜底 ──
    if not result['txt'] and txt_raw_lines:
        raw_text = '\n'.join(txt_raw_lines).strip()
        if raw_text:
            result['txt'] = raw_text
    if not result['txt'] and not txt_raw_lines:
        bare = '\n'.join(
            l for l in sse_text.split('\n')
            if l.strip() and not l.strip().startswith('[') and not l.strip().startswith('@@')
            and not l.strip().startswith('event:') and l.strip() != '>>>>>>>>>>'
        ).strip()
        if bare:
            result['txt'] = bare

    # ── ANSWER 兜底 ──
    if not direct_fields.get('ANSWER') and not result.get('answer'):
        has_pending = bool(commands or result.get('tool_calls') or result.get('plan') or result.get('ask'))
        result['answer'] = 'no' if has_pending else 'yes'

    # ── 填充直接字段 ──
    field_mapping = {
        'ANSWER': 'answer',
        'ANALYSIS': 'analysis',
        'ASK': 'ask',
        'MEMORY': 'memory',
        'TAG': 'tag',
        'CLASS': 'class',
        'SLEEP': 'sleep',
        'PROMPT': 'prompt',
    }
    for sse_field, result_key in field_mapping.items():
        if sse_field in direct_fields:
            val = direct_fields[sse_field]
            if sse_field == 'SLEEP':
                try:
                    result[result_key] = int(val)
                except (ValueError, TypeError):
                    result[result_key] = None
            else:
                result[result_key] = val

    for idx, cmd in enumerate(commands, 1):
        result[f"cmd{idx}"] = cmd

    return result


def _parse_ai_raw_response(raw_text: str) -> Dict[str, Any]:
    """解析 AI 原始响应文本，提取 [TXT]...[TXT:DONE] 等内嵌标记。"""
    result = {
        "answer": "",
        "ask": "",
        "analysis": "",
        "txt": "",
        "tag": "",
        "memory": "",
        "plan": "",
        "class": "",
        "tool_calls": [],
    }

    lines = raw_text.split('\n')
    i = 0
    while i < len(lines):
        stripped = lines[i].rstrip('\r').strip()
        i += 1

        if stripped == '[TXT]' or (stripped.startswith('[TXT]') and not stripped.startswith('[TXT:DONE]') and not stripped.startswith('[TXT]:')):
            txt_lines = []
            if stripped != '[TXT]':
                inline = stripped[5:]
                done_pos = inline.find('[TXT:DONE]')
                if done_pos >= 0:
                    if done_pos > 0:
                        txt_lines.append(inline[:done_pos])
                    remainder = inline[done_pos + 10:]
                    if remainder:
                        lines.insert(i, remainder)
                else:
                    if inline:
                        txt_lines.append(inline)
                    while i < len(lines):
                        next_stripped = lines[i].rstrip('\r').strip()
                        i += 1
                        done_pos = next_stripped.find('[TXT:DONE]')
                        if done_pos >= 0:
                            if done_pos > 0:
                                txt_lines.append(next_stripped[:done_pos])
                            remainder = next_stripped[done_pos + 10:]
                            if remainder:
                                lines.insert(i, remainder)
                            break
                        txt_lines.append(lines[i - 1])
            else:
                while i < len(lines):
                    next_stripped = lines[i].rstrip('\r').strip()
                    i += 1
                    done_pos = next_stripped.find('[TXT:DONE]')
                    if done_pos >= 0:
                        if done_pos > 0:
                            txt_lines.append(next_stripped[:done_pos])
                        remainder = next_stripped[done_pos + 10:]
                        if remainder:
                            lines.insert(i, remainder)
                        break
                    txt_lines.append(lines[i - 1])
            result['txt'] = '\n'.join(txt_lines).strip()
            continue

        if stripped.startswith('[TXT:DONE]'):
            remainder = stripped[10:]
            if remainder:
                lines.insert(i, remainder)
            continue

        # [PROMPT]...[PROMPT:DONE], [TAG]...[TAG:DONE], [ANALYSIS]...[ANALYSIS:DONE], [PLAN]...[PLAN:DONE]
        for tag, key, taglen in (('[PROMPT]', 'prompt', 13), ('[TAG]', 'tag', 10),
                                  ('[ANALYSIS]', 'analysis', 15), ('[PLAN]', 'plan', 11)):
            if stripped == tag:
                collected = []
                while i < len(lines):
                    ns = lines[i].rstrip('\r').strip()
                    i += 1
                    done_marker = f'[{key.upper()}:DONE]'
                    done_pos = ns.find(done_marker)
                    if done_pos >= 0:
                        if done_pos > 0:
                            collected.append(ns[:done_pos])
                        rem = ns[done_pos + taglen:]
                        if rem:
                            lines.insert(i, rem)
                        break
                    collected.append(lines[i - 1])
                result[key] = '\n'.join(collected).strip()
                break
            if stripped.startswith(tag) and not stripped.startswith(f'[{key.upper()}:DONE]') and not stripped.startswith(f'{tag}:') and f'[{key.upper()}:DONE]' in stripped:
                inline = stripped[len(tag):]
                done_marker = f'[{key.upper()}:DONE]'
                done_pos = inline.find(done_marker)
                if done_pos > 0:
                    result[key] = inline[:done_pos].strip()
                rem = inline[done_pos + taglen:] if done_pos >= 0 else ''
                if rem:
                    lines.insert(i, rem)
                break
        else:
            # no break in for — no multi-line block matched
            pass
        if stripped.startswith('[PROMPT:DONE]') or stripped.startswith('[ANALYSIS:DONE]') or stripped.startswith('[PLAN:DONE]') or stripped.startswith('[TAG:DONE]'):
            continue

        # [plan]...[plan:done] 块
        if stripped == '[plan]':
            plan_lines = []
            while i < len(lines) and lines[i].rstrip('\r').strip() != '[plan:done]':
                plan_lines.append(lines[i])
                i += 1
            result['plan'] = '\n'.join(plan_lines).strip()
            i += 1
            continue

        # [tool:NAME PARAMS]...[tool:NAME:done] 块
        tool_match = re.match(r'^\[tool:(\S+)\s*(.*)\]$', stripped)
        if tool_match:
            tool_name = tool_match.group(1)
            tool_params = tool_match.group(2).strip()
            tool_body_lines = []
            done_marker = f'[tool:{tool_name}:done]'
            while i < len(lines) and lines[i].rstrip('\r').strip() != done_marker:
                tool_body_lines.append(lines[i])
                i += 1
            result['tool_calls'].append({
                'name': tool_name,
                'params_str': tool_params,
                'body': '\n'.join(tool_body_lines).strip(),
            })
            i += 1
            continue

        fm = re.match(r'^\[(ANALYSIS|ANSWER|ASK|MEMORY|TAG|CLASS|SLEEP)\]:', stripped)
        if fm:
            name = fm.group(1)
            value = stripped[fm.end():].strip()
            result['fields'][name] = value  # 同名覆盖，最后一个是最终值
            continue

    return result


def _parse_legacy_shell(lines: List[str], start_i: int) -> Dict[str, Any]:
    """解析 @@SHELL 格式。"""
    result = {'commands': [], 'fields': {}, 'tool_calls': [], 'plan': ''}
    i = start_i
    cmd_lines = None

    def _flush_cmd():
        nonlocal cmd_lines
        if cmd_lines is not None:
            cmd = '\n'.join(cmd_lines).strip()
            if cmd:
                result['commands'].append(cmd)
            cmd_lines = None

    while i < len(lines):
        stripped = lines[i].rstrip('\r').strip()
        i += 1

        if stripped.startswith('@@SHELL'):
            _flush_cmd()
            remainder = stripped[7:]
            if remainder:
                lines.insert(i, remainder)
            continue

        if stripped.startswith('>>>>>>>>>>'):
            if cmd_lines is None:
                cmd_lines = []
            else:
                _flush_cmd()
            continue

        if cmd_lines is not None:
            if stripped and not stripped.startswith('['):
                cmd_lines.append(stripped)
            continue

        if stripped == '[TXT]':
            txt_lines = []
            while i < len(lines) and lines[i].rstrip('\r').strip() != '[TXT:DONE]':
                txt_lines.append(lines[i])
                i += 1
            result['fields']['TXT'] = '\n'.join(txt_lines).strip()
            i += 1
            continue

        if stripped == '[plan]':
            plan_lines = []
            while i < len(lines) and lines[i].rstrip('\r').strip() != '[plan:done]':
                plan_lines.append(lines[i])
                i += 1
            result['plan'] = '\n'.join(plan_lines).strip()
            i += 1
            continue

        tool_match = re.match(r'^\[tool:(\S+)\s*(.*)\]$', stripped)
        if tool_match:
            tool_name = tool_match.group(1)
            tool_params = tool_match.group(2).strip()
            tool_body_lines = []
            done_marker = f'[tool:{tool_name}:done]'
            while i < len(lines) and lines[i].rstrip('\r').strip() != done_marker:
                tool_body_lines.append(lines[i])
                i += 1
            result['tool_calls'].append({
                'name': tool_name,
                'params_str': tool_params,
                'body': '\n'.join(tool_body_lines).strip(),
            })
            i += 1
            continue

        fm = re.match(r'^\[(ANALYSIS|ANSWER|ASK|MEMORY|TAG|CLASS|SLEEP)\]:', stripped)
        if fm:
            name = fm.group(1)
            value = stripped[fm.end():].strip()
            result['fields'][name] = value
            continue

    _flush_cmd()
    return result

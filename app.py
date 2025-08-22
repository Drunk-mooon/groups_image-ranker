# app.py (group-only, 精简版)
from flask import Flask, render_template, request, jsonify, send_file, Response
import os, secrets
from datetime import timedelta
import json
import csv
import threading
from threading import Thread
from datetime import datetime
import logging
from flask import Flask, request, session, jsonify
import random
from collections import OrderedDict
import io

logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)

# 全局配置
IMAGE_FOLDER = 'static/images'   # 默认图片目录（可通过 UI 选择）
RESULTS_JSON = './results.json'
current_directory = None

# groups 数据结构与锁
image_groups = []    # 每个 group: {'id': int, 'instruction': str, 'images': [abs_path,...]}
current_group_index = 0
image_groups_lock = threading.Lock()
# 全局：呈现序列（决定随机呈现哪些组、顺序）
presentation_sequence = []

# 默认每组 N 张（如果没有 groups.json，则按此划分）
DEFAULT_GROUP_SIZE = 6

# ---------- 工具函数 ----------
def load_groups_from_json_file(directory):
    """如果 directory 下存在 groups.json，优先读取并解析（支持相对路径）。"""
    groups_path = os.path.join(directory, 'groups.json')
    if not os.path.exists(groups_path):
        return []
    try:
        with open(groups_path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        groups = []
        for i, g in enumerate(raw):
            instr = g.get('instruction', '')
            instr_cn = g.get('instruction_cn', '')
            imgs = []
            for p in g.get('images', []):
                if not os.path.isabs(p):
                    p = os.path.join(directory, p)
                p = os.path.normpath(p).replace('\\', '/')
                if os.path.exists(p):
                    imgs.append(p)
            ref_img = g.get('reference_image', '')
            if ref_img:
                if not os.path.isabs(ref_img):
                    ref_img = os.path.join(directory, ref_img)
                ref_img = os.path.normpath(ref_img).replace('\\', '/')
                if not os.path.exists(ref_img):
                    ref_img = ''  # 不存在则置空
            groups.append({'id': i, 
                           'instruction': instr, 
                           'instruction_cn': instr_cn,
                           'images': imgs, 
                           'reference_image': ref_img})
        return groups
    except Exception as e:
        app.logger.error(f"Failed to parse groups.json: {e}")
        return []

def auto_create_groups_from_directory(directory, group_size=DEFAULT_GROUP_SIZE):
    """当没有 groups.json 时，自动按照 group_size 把目录内图片分块生成 groups。"""
    image_paths = []
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.jfif', '.avif', '.heic', '.heif')):
                p = os.path.join(root, file)
                p = os.path.normpath(p).replace('\\', '/')
                image_paths.append(p)
    image_paths.sort()
    groups = []
    if not image_paths:
        return groups
    gid = 0
    for i in range(0, len(image_paths), group_size):
        imgs = image_paths[i:i+group_size]
        groups.append({'id': gid, 
                       'instruction': f'instruction loss for group {gid}',
                       'instruction_cn': '错误：命令丢失', 
                       'images': imgs})
        gid += 1
    return groups

def initialize_image_groups(directory=None, group_size=DEFAULT_GROUP_SIZE):
    """初始化 image_groups，优先使用 groups.json，否则自动分块。"""
    global image_groups, current_group_index, current_directory, presentation_sequence
    with image_groups_lock:
        image_groups = []
        current_group_index = 0
        if directory:
            current_directory = directory
        if not current_directory:
            current_directory = IMAGE_FOLDER
        groups = load_groups_from_json_file(current_directory)
        if not groups:
            groups = auto_create_groups_from_directory(current_directory, group_size)
        # 只打乱每组内部图片顺序（保证图片每次随机展示）
        for g in groups:
            random.shuffle(g['images'])
        # 不打乱 groups 列表本身 —— 保持 groups 的原始顺序（对应 groups.json）
        # random.shuffle(groups)
        # for i, g in enumerate(groups):
        #     g['id'] = i

        image_groups = groups

        # 构造一个随机的呈现顺序（presentation_sequence），其元素是 image_groups 的索引
        presentation_sequence = list(range(len(image_groups)))
        random.shuffle(presentation_sequence)

        current_group_index = 0
        app.logger.info(f'Initialized {len(image_groups)} groups (dir={current_directory}), presentation order ready')

# ---------- 路由：前端页面 ----------
@app.route('/')
def index():
    # 将主页指向分组排序界面
    return render_template('sort.html')

@app.route('/sort')
def sort_index():
    return render_template('sort.html')

# ---------- 路由：API ----------
@app.route('/get_group/<int:group_id>')
def get_group(group_id):
    """
    参数 group_id 表示 presentation index（第几次/第几个被呈现的组）。
    返回里：
      - 'id' 为 presentation index（前端用于 prev/next）
      - 'orig_id' 为固定的原始 group id（来自 groups.json 的顺序）
    """
    with image_groups_lock:
        if group_id < 0 or group_id >= len(presentation_sequence):
            return jsonify({'error': 'Invalid group_id or no more groups'}), 400
        orig_idx = presentation_sequence[group_id]
        g = image_groups[orig_idx]
        # 每次获取时打乱图片顺序（图片在组内随机）
        images_copy = g.get('images', []).copy()
        random.shuffle(images_copy)
        return jsonify({
            'id': group_id,                     # presentation index（用于导航）
            'orig_id': g.get('id'),             # 固定原始组 id（用于保存）
            'instruction': g.get('instruction', ''),
            'instruction_cn': g.get('instruction_cn', ''),
            'images': images_copy,
            'reference_image': g.get('reference_image', ''),
            'total_groups': len(presentation_sequence)
        })

@app.route('/get_next_group')
def get_next_group():
    global current_group_index
    with image_groups_lock:
        if current_group_index >= len(presentation_sequence):
            return jsonify({'error': 'No more groups'}), 400
        gid = current_group_index
        current_group_index += 1
    return get_group(gid)

app.secret_key = "your_secret_key"

@app.route('/set_user', methods=['POST'])
def set_user():
    data = request.get_json() or {}
    uid = (data.get('user_id') or '').strip()
    if not uid:
        return jsonify({'error': 'user_id required'}), 400
    session['user_id'] = uid
    session.permanent = True  # 使用 PERMANENT_SESSION_LIFETIME
    return jsonify({'success': True, 'user_id': uid})

@app.route('/whoami', methods=['GET'])
def whoami():
    return jsonify({'user_id': session.get('user_id')})

@app.route('/logout_user', methods=['POST'])
def logout_user():
    session.pop('user_id', None)
    return jsonify({'success': True})

@app.route('/submit_group', methods=['POST'])
def submit_group():
    data = request.json
    group_id = data.get('group_id')
    sorted_images = data.get('sorted_images', [])
    instruction = data.get('instruction', '')
    user_id = session.get('user_id', 'anonymous')

    if group_id is None or not isinstance(sorted_images, list):
        return jsonify({'error': 'Invalid payload'}), 400

    out_dir = current_directory if current_directory else '.'
    results_file = os.path.join(out_dir, 'results.csv')
    timestamp = datetime.now().isoformat()
    try:
        new_file = not os.path.exists(results_file)
        with open(results_file, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if new_file:
                writer.writerow(['timestamp', 'group_id', 'instruction', 'user_id', 'sorted_images_joined'])
            writer.writerow([timestamp, group_id, instruction, user_id, '|'.join(sorted_images)])
        app.logger.info(f'Saved group {group_id} result by {user_id} to {results_file}')
        return jsonify({'success': True})
    except Exception as e:
        app.logger.error(f'Failed to save results: {e}')
        return jsonify({'error': str(e)}), 500

@app.route('/get_groups_count')
def get_groups_count():
    with image_groups_lock:
        return jsonify({'total_groups': len(presentation_sequence), 'current_index': current_group_index})

@app.route('/serve_image')
def serve_image():
    image_path = request.args.get('path')
    if not image_path:
        return jsonify({'error': 'No path provided'}), 400
    # 浏览器端会传入 encodeURIComponent 的路径
    image_path = image_path
    # 如果前端传来的路径是以 /serve_image 开头（历史问题），尝试修复
    if image_path.startswith('/serve_image'):
        image_path = image_path.split('=', 1)[1]
    image_path = os.path.normpath(image_path).replace('\\', '/')
    if not os.path.exists(image_path):
        return jsonify({'error': f'File not found: {image_path}'}), 404
    file_extension = os.path.splitext(image_path)[1].lower()
    if file_extension == '.webp':
        mimetype = 'image/webp'
    else:
        mimetype = 'image/jpeg'
    return send_file(image_path, mimetype=mimetype)

# 选择目录（保留原有的 tkinter 弹窗机制）
@app.route('/select_directory', methods=['POST'])
def select_directory():
    try:
        def directory_selection():
            nonlocal directory
            directory = open_directory_dialog()

        directory = None
        thread = Thread(target=directory_selection)
        thread.start()
        thread.join()

        if directory:
            global IMAGE_FOLDER
            IMAGE_FOLDER = directory
            initialize_image_groups(directory)
            return jsonify({'success': True, 'directory': directory})
        else:
            return jsonify({'success': False, 'error': 'No directory selected'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

def open_directory_dialog():
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    directory = filedialog.askdirectory(master=root)
    root.destroy()
    return directory

@app.route('/reset_progress', methods=['POST'])
def reset_progress():
    global current_group_index
    with image_groups_lock:
        current_group_index = 0
    return jsonify({'success': True})
##### csv ######
# @app.route('/submit_all', methods=['POST'])
# def submit_all():
#     data = request.json
#     results = data.get('results', [])
#     user_id = session.get('user_id', 'anonymous')
#     out_dir = current_directory if current_directory else '.'
#     results_file = os.path.join(out_dir, 'results.csv')
#     timestamp = datetime.now().isoformat()

#     if not isinstance(results, list):
#         return jsonify({'error': 'Invalid results payload'}), 400

#     try:
#         new_file = not os.path.exists(results_file)
#         with open(results_file, 'a', newline='', encoding='utf-8') as f:
#             writer = csv.writer(f)
#             if new_file:
#                 writer.writerow(['timestamp', 'group_id', 'instruction', 'user_id', 'sorted_images_joined'])
#             for r in results:
#                 writer.writerow([
#                     timestamp,
#                     r.get('group_id'),
#                     r.get('instruction', ''),
#                     user_id,
#                     '|'.join(r.get('sorted_images', []))
#                 ])
#         return jsonify({'success': True})
#     except Exception as e:
#         app.logger.error(f'Failed to save all results: {e}')
#         return jsonify({'error': str(e)}), 500

@app.route('/submit_all', methods=['POST'])
def submit_all():
    """
    接收前端传来的 results 列表（每个元素包含 group_id, instruction, instruction_cn,
    sorted_images, started_at, submitted_at, time_spent_seconds 等），将这次提交以如下格式追加到 RESULTS_JSON:
    {
      "user_ids": [ ... ],   # 允许重复，按提交顺序追加
      "all_data": [
        {
          "user_id": "...",
          "labeled_data": {
            "0": [ {record}, ... ],
            "1": [ ... ]
          }
        },
        ...
      ]
    }
    """
    data = request.json
    results = data.get('results', [])
    user_id = session.get('user_id', 'anonymous')
    out_dir = current_directory if current_directory else '.'
    results_path = os.path.join(out_dir, RESULTS_JSON)

    if not isinstance(results, list):
        return jsonify({'error': 'Invalid results payload'}), 400

    try:
        # 读取已有文件（若存在并且是 dict 则载入，否则初始化空结构）
        if os.path.exists(results_path):
            try:
                with open(results_path, 'r', encoding='utf-8') as f:
                    existing = json.load(f)
                if not isinstance(existing, dict):
                    existing = {'user_ids': [], 'all_data': []}
                else:
                    # 兼容缺失键
                    if 'user_ids' not in existing or not isinstance(existing['user_ids'], list):
                        existing['user_ids'] = []
                    if 'all_data' not in existing or not isinstance(existing['all_data'], list):
                        existing['all_data'] = []
            except Exception:
                # 读取失败则重建结构
                existing = {'user_ids': [], 'all_data': []}
        else:
            existing = {'user_ids': [], 'all_data': []}

        # 构造本次提交的 labeled_data：按 group_id 聚合为 dict（key 为字符串）
        labeled_data = {}
        for r in results:
            gid = r.get('group_id')
            key = str(gid) if gid is not None else 'unknown_group'
            record = {
                'group_id': gid,
                'instruction': r.get('instruction', ''),
                'instruction_cn': r.get('instruction_cn', ''),
                'sorted_images': r.get('sorted_images', []),
                'started_at': r.get('started_at'),
                'submitted_at': r.get('submitted_at'),
                'time_spent_seconds': r.get('time_spent_seconds')
            }
            labeled_data.setdefault(key, []).append(record)

        # 将 user_id 追加到 user_ids（允许重复）
        existing['user_ids'].append(user_id)

        # 将本次提交加入 all_data（每次一个 entry）
        existing['all_data'].append({
            'user_id': user_id,
            'labeled_data': labeled_data
        })

        # 覆盖写回文件（只包含 user_ids 与 all_data 两个顶层 key）
        output = {
            'user_ids': existing['user_ids'],
            'all_data': existing['all_data']
        }
        with open(results_path, 'w', encoding='utf-8') as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        app.logger.info(f"Saved {len(results)} results for user {user_id} to {results_path}")
        return jsonify({'success': True, 'saved_to': results_path})
    except Exception as e:
        app.logger.error(f'Failed to save results (json): {e}', exc_info=True)
        return jsonify({'error': str(e)}), 500


@app.route('/export_results_json')
def export_results_json():
    out_dir = current_directory if current_directory else '.'
    results_path = os.path.join(out_dir, RESULTS_JSON)
    if not os.path.exists(results_path):
        return jsonify({'error': 'No results file found'}), 404
    try:
        # Flask 版本差异兼容
        return send_file(results_path, as_attachment=True, download_name=os.path.basename(results_path))
    except TypeError:
        return send_file(results_path, as_attachment=True, attachment_filename=os.path.basename(results_path))

# 可选：导出已收集的 results.csv
@app.route('/export_results')
def export_results():
    out_dir = current_directory if current_directory else '.'
    results_file = os.path.join(out_dir, 'results.csv')
    if not os.path.exists(results_file):
        return jsonify({'error': 'No results file found'}), 404
    return send_file(results_file, as_attachment=True, attachment_filename='results.csv')

# ---------- 程序入口 ----------
if __name__ == '__main__':
    # 初始化 groups（默认使用 IMAGE_FOLDER）
    initialize_image_groups(IMAGE_FOLDER)
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)

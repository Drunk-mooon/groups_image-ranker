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

logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)

# 全局配置
IMAGE_FOLDER = 'static/images'   # 默认图片目录（可通过 UI 选择）
current_directory = None

# groups 数据结构与锁
image_groups = []    # 每个 group: {'id': int, 'instruction': str, 'images': [abs_path,...]}
current_group_index = 0
image_groups_lock = threading.Lock()

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
            groups.append({'id': i, 'instruction': instr, 'images': imgs, 'reference_image': ref_img})
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
        groups.append({'id': gid, 'instruction': f'Default instruction for group {gid}', 'images': imgs})
        gid += 1
    return groups

def initialize_image_groups(directory=None, group_size=DEFAULT_GROUP_SIZE):
    """初始化 image_groups，优先使用 groups.json，否则自动分块。"""
    global image_groups, current_group_index, current_directory
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
        for g in groups:
            random.shuffle(g['images'])
        # 组的顺序随机
        random.shuffle(groups)
        for i, g in enumerate(groups):
            g['id'] = i
        image_groups = groups
        app.logger.info(f'Initialized {len(image_groups)} groups (dir={current_directory})')

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
    with image_groups_lock:
        if group_id < 0 or group_id >= len(image_groups):
            return jsonify({'error': 'Invalid group_id or no more groups'}), 400
        g = image_groups[group_id]
        return jsonify({
            'id': g['id'],
            'instruction': g.get('instruction', ''),
            'images': g.get('images', []),
            'reference_image': g.get('reference_image', ''),  # <--- 加上这行
            'total_groups': len(image_groups)
        })

@app.route('/get_next_group')
def get_next_group():
    global current_group_index
    with image_groups_lock:
        if current_group_index >= len(image_groups):
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
        return jsonify({'total_groups': len(image_groups), 'current_index': current_group_index})

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
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True)

import sys
import json
import os
from merge_jsons import merge_jsons

if __name__ == "__main__":
    json1_path = sys.argv[1]
    json2_path = sys.argv[2]
    err_list = []

    with open(json1_path, 'r', encoding='utf-8') as f:
        json1 = json.load(f)
    with open(json2_path, 'r', encoding='utf-8') as f:
        json2 = json.load(f)

result = merge_jsons(json1, json2, err_list) # Здесь ваш словарь или список, который нужно сохранить

# Получить путь к текущей папке (где находится main.py)
current_dir = os.path.dirname(os.path.abspath(__file__))

# Имя файла для сохранения
filename = "merged_master.json"

# Полный путь к файлу
filepath = os.path.join(current_dir, filename)

# Сохранить результат в файл
with open(filepath, "w", encoding="utf-8") as f:
    json.dump(result, f, ensure_ascii=False, indent=4)


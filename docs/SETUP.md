# Установка по шагам

## 1. Файлы

```powershell
git clone https://github.com/<логин>/hermes-office.git
cd hermes-office
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

Что появится:

```
%LOCALAPPDATA%\hermes\
├─ office-live\
│  ├─ office_live.py     фид (порт 8797, только loopback)
│  ├─ office.html        вьювер, 9.7 МБ, модель внутри
│  └─ README.md
└─ desktop-plugins\office-hermes\plugin.js
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\hermes-office.vbs
```

## 2. Hermes

`Ctrl+K` → **Reload desktop plugins**. Это обязательно: JS плагинов кешируется до перезагрузки.

После перезагрузки:

* панель `office_hermes` открывается справа от чата сама;
* если закрыли — чип `office_hermes ◉N` в статусбаре или команда `office_hermes: открыть офис`
  в палитре (`Ctrl+K`).

## 3. Проверка

```powershell
curl http://127.0.0.1:8797/health
# {"ok":true,"version":"1.0.0","home":"C:\\Users\\...\\hermes","db":true,...,"hits":{...}}
```

`db: true` — база найдена. Откройте `http://127.0.0.1:8797/` в браузере: офис должен
показать столы текущего чата.

## 4. Автозапуск

Установщик создаёт `Startup\hermes-office.vbs` (тихий `pythonw`, без окна консоли).
Убрать: `-NoAutostart` при установке или удалить файл.

## 5. Другой порт

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1 -Port 8801
```
Установщик перепишет `BASE` в установленной копии `plugin.js` и автозапуск.
Если правите вручную — меняйте в двух местах: `BASE` в `plugin.js` и `--port` у сервера.

## 6. Свежая машина: чек-лист

1. Hermes desktop установлен и хотя бы раз запущен (появился `state.db`).
2. Python 3.10+ в PATH или указан `-PythonExe`.
3. `git clone` + `install.ps1`.
4. `Ctrl+K` → Reload desktop plugins.

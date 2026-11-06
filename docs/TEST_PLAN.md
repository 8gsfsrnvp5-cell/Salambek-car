# План тестирования

Автотесты: `pytest` (папка `tests`), запускаются локально (`scripts/build.ps1`) и в GitHub Actions перед каждой сборкой.
Установленная программа дополнительно проверяется командой `AutoServiceAI.exe --self-test` и в CI-сценарии
«установка → самопроверка → обновление → удаление».

## Обязательные сценарии ТЗ (раздел 25)

| № | Сценарий | Тест |
|---|---|---|
| 1 | Создание запчасти | `test_core_flow::test_01_02_create_part_and_purchase_10` |
| 2 | Закупка 10 единиц | там же |
| 3–6 | Наценка 0 %, 30 %, 100 %, произвольная | `test_03_06_markup_on_sale[0/30/100/17.5]`, `test_domain_money::test_markup_formula` |
| 7–8 | Продажа части партии, остаток | `test_07_10_partial_sale_second_price_fifo` |
| 9–10 | Вторая закупка по другой цене, FIFO | там же |
| 11–13 | Клиент, автомобиль, повторный поиск | `test_11_13_customer_vehicle_and_search` |
| 14–18 | Заказ-наряд, работы, запчасти, резерв/списание, закрытие | `test_14_18_work_order_full_cycle` |
| 19 | Частичная оплата | `test_19_partial_payment` |
| 20 | Возврат запчасти | `test_20_return_from_customer_restores_fifo_cost` |
| 21 | Отмена ошибочной операции | `test_21_cancel_erroneous_operations`, `test_payment_cancel_keeps_history` |
| 22 | Создание PDF | `test_documents_reports::test_22_pdf_documents_with_cyrillic` |
| 23 | Печать документа | `test_23_print_document_through_printer_path` (вывод через QPrinter на виртуальный PDF-принтер) |
| 24 | Экспорт Excel | `test_24_excel_and_csv_export` |
| 25 | Формирование отчёта | `test_25_all_reports_build_and_export[15 отчётов]`, `test_financial_summary_numbers` |
| 26 | Некорректная накладная | `test_ai_documents::test_26_invalid_invoice_handled` |
| 27 | Повторный импорт документа | `test_27_repeated_import_of_same_document` |
| 28 | Резервное копирование | `test_backup_migration::test_28_backup_and_verify` |
| 29–30 | Восстановление, перенос на другой компьютер | `test_29_30_restore_on_another_computer` |
| 31 | Работа без интернета | `test_31_works_without_network` (все сокеты заблокированы) |
| 32 | Сохранение данных после перезапуска | `test_32_data_survives_restart` |
| 33 | Обновление с существующей базой | `test_33_update_existing_database_keeps_data`, CI: установка обновления поверх |

## Дополнительно

* Округления: `test_round_half_up`, `test_allocate_keeps_every_kopeck`, `test_fifo_no_kopeck_loss_on_repeated_partial`.
* Повторное проведение: `test_purchase_cannot_be_posted_twice_and_duplicates_blocked`.
* Атомарность: `test_sale_insufficient_stock_rolls_back`.
* Отказ диска / нехватка места / повреждённые архивы: `test_disk_unavailable_and_no_space`, `test_corrupted_archive_rejected`,
  `test_encrypted_backup_requires_password`.
* Неудачное обновление базы: `test_failed_update_rolls_back`, `test_newer_database_refused`.
* Ошибки OCR и сомнительные совпадения: `test_ocr_errors_are_flagged_not_hidden`, `test_name_similarity_never_auto_matches`.
* ИИ: уровни достоверности, отсутствие выдуманных запчастей, критичные системы, локальность LLM-адаптера.
* Права ролей: `test_manager_permissions`, `test_restore_requires_admin`, `test_manager_cannot_open_finance_reports`.
* Интерфейс: `test_ui_smoke` — все 12 разделов и основные диалоги на реальных данных.

## Ручные проверки перед выдачей версии

1. Установка на чистый компьютер с Windows 11 без Python (или виртуальную машину), запуск с ярлыка.
2. Печать на реальный принтер A4 и на чековый принтер 58/80 мм.
3. Копия на реальный внешний диск; восстановление на втором компьютере.
4. Распознавание реальных накладных поставщиков заказчика (PDF/Excel; сканы — при установленном Tesseract).
5. Режим B — на целевом оборудовании после установки выбранной модели.

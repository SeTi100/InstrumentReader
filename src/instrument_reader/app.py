import logging
import os
import sys
from PySide6.QtWidgets import QApplication, QMessageBox
from .core import data_safety
from .gui.main_window import MainWindow


def prepare_data(parent=None) -> bool:
    """Sets up the data folder before anything is written. False: do not start."""
    try:
        folder = data_safety.ensure_data_dir()
    except data_safety.DataDirInRepoError as e:
        QMessageBox.critical(parent, "Datenordner im Git-Ordner", str(e))
        return False
    db = folder / data_safety.DB_NAME

    copied = data_safety.migrate_legacy_db(os.getcwd(), db)
    if copied is not None:
        QMessageBox.information(
            parent, "Datenbank übernommen",
            f"Die Datenbank wurde nach\n{db}\nkopiert und wird ab jetzt dort benutzt.\n\n"
            f"Die alte Datei {copied} bleibt unverändert liegen; du kannst sie später selbst löschen.")
    elif os.path.exists(data_safety.DB_NAME) and os.path.abspath(data_safety.DB_NAME) != str(db.resolve()):
        logging.getLogger(__name__).warning(
            "Im Startordner liegt noch %s; benutzt wird %s.", os.path.abspath(data_safety.DB_NAME), db)

    problem = data_safety.check_state(db)
    if problem:
        backup = data_safety.latest_backup(db)
        box = QMessageBox(QMessageBox.Warning, "Datenbank unvollständig", problem, parent=parent)
        restore = box.addButton("Neuestes Backup einspielen", QMessageBox.AcceptRole) if backup else None
        go_on = box.addButton("Trotzdem so starten", QMessageBox.DestructiveRole)
        box.addButton("Beenden", QMessageBox.RejectRole)
        box.setInformativeText(
            (f"Neuestes Backup: {backup.name}\n" if backup else "Kein Backup gefunden.\n")
            + f"Aus dem Journal neu aufbauen: scripts/rebuild_db.py (Journal in {data_safety.journal_dir_for(db)}).")
        box.exec()
        clicked = box.clickedButton()
        if restore is not None and clicked is restore:
            kept = data_safety.restore_backup(backup, db)
            QMessageBox.information(parent, "Backup eingespielt",
                                    f"{backup.name} ist jetzt die aktive Datenbank."
                                    + (f"\nDie vorherige liegt als {kept.name} daneben." if kept else ""))
        elif clicked is not go_on:
            return False

    data_safety.backup_database(db, "start")
    data_safety.save_state(db)
    return True


def main():
    app = QApplication(sys.argv)
    if not prepare_data():
        sys.exit(1)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()

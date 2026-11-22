"""Arranque, copia local de la credencial y bloqueo de dos instancias."""
import os
import importlib.util
import stat
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from contextlib import redirect_stdout
import main


class Inicio(unittest.TestCase):
    def setUp(self):
        self.d=TemporaryDirectory();self.addCleanup(self.d.cleanup)
        ctx=patch.dict(os.environ,{"AVARAID_DATOS":self.d.name,"DISCORD_TOKEN":""})
        ctx.start();self.addCleanup(ctx.stop)
        # La prueba no debe modificar ni quitar el .env real incluido en el ZIP.
        file_patch=patch.object(main,"archivo_token_incluido",return_value=Path(self.d.name)/".env")
        file_patch.start();self.addCleanup(file_patch.stop)

    @unittest.skipUnless(importlib.util.find_spec("discord") is not None, "requiere discord.py")
    def test_comprobar_no_pide_token_ni_deja_datos_personales(self):
        with patch.object(main,"obtener_token",side_effect=AssertionError("no se debe leer")), redirect_stdout(StringIO()) as out:
            self.assertEqual(main.main(["--comprobar"]),0)
        self.assertIn("19 comandos",out.getvalue())
        self.assertEqual(list(Path(self.d.name).iterdir()),[])

    def test_credencial_y_prioridad_de_variable_sin_imprimir_secreto(self):
        ficticio="a"*45
        with patch.object(main.getpass,"getpass",return_value=ficticio),redirect_stdout(StringIO()) as out:
            self.assertEqual(main.main(["--configurar"]),0)
        self.assertNotIn(ficticio,out.getvalue())
        self.assertEqual(main.obtener_token(),ficticio)
        if os.name!="nt":self.assertEqual(stat.S_IMODE(main.archivo_token().stat().st_mode)&0o077,0)
        with patch.dict(os.environ,{"DISCORD_TOKEN":"z"*35}):self.assertEqual(main.obtener_token(),"z"*35)

    def test_segunda_instancia_no_programa_otro_recorrido(self):
        d=Path(self.d.name)
        with main.unica_instancia(d):
            with self.assertRaisesRegex(ValueError,"ya está abierto"):
                with main.unica_instancia(d):self.fail("permitió otro proceso")
        with main.unica_instancia(d):pass

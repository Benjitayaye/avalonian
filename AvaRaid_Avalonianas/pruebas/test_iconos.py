"""Comprueba la configuración de emojis y el repintado sin conexión a Discord."""
import ast
import re
import tempfile
import unittest
from pathlib import Path
from raids import Almacen


class EmojisRoles(unittest.TestCase):
    def test_ocho_roles_y_ids_sin_duplicados(self):
        ruta=Path(__file__).resolve().parents[1]/"panel.py"
        tree=ast.parse(ruta.read_text(encoding="utf-8"))
        mapping=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign)
                     and any(isinstance(t,ast.Name) and t.id=="ICONOS" for t in n.targets))
        esperados={
            "Falce":"1573894790687359118",
            "Main Tank":"1573894654338928721",
            "ShadowCaller":"1573895312626679818",
            "Off Tank":"1573894762749104208",
            "Falce Daga":"1573895755012247713",
            "Healer":"1573894688513986611",
            "Scout":"1573895471443873902",
            "Great Arcane":"1573895348462551050",
        }
        ids=[]
        for rol,uid in esperados.items():
            self.assertRegex(mapping[rol],rf"^<:[a-z_]+:{uid}>$")
            ids.append(uid)
        self.assertEqual(len(ids),len(set(ids)))
        self.assertEqual(mapping["Pierce"],"🎯")

    def test_refrescar_paneles_anteriores(self):
        with tempfile.TemporaryDirectory() as tmp:
            ahora=1800000000
            db=Almacen(Path(tmp)/"prueba.db",lambda:ahora)
            raid=db.crear(1,11,22,"roles", "normal", ahora+3600)
            db.publicar(raid,123)
            revision=db.ficha(1,raid)["revision"]
            db.pintada(raid,revision)
            self.assertEqual(db.sucias(),[])
            db.refrescar_iconos()
            self.assertIn((1,raid),db.sucias())
            self.assertEqual(db.ficha(1,raid)["revision"],revision)

if __name__=="__main__":unittest.main()

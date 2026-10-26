import json

import pytest

from app.backtest.study import (
    FINAL_PATH,
    HYPOTHESES,
    MAX_DRAWDOWN_PCT,
    MIN_TRADES,
    VARIANTS,
    BlockSummary,
    FinalTestRefused,
    check_final_allowed,
    criteria,
    passes,
    retained,
    select,
    variant_config,
)


def summary(variant="V0", block="learning", **over) -> BlockSummary:
    base = {
        "variant": variant,
        "block": block,
        "return_pct": 5.0,
        "benchmark_scaled_pct": 2.0,
        "max_drawdown_pct": 8.0,
        "profit_factor": 1.5,
        "trades": 100,
        "avg_exposure_pct": 15.0,
        "fees_paid": 100.0,
        "win_rate_pct": 40.0,
        "signals": 200,
        "by_reason": {},
    }
    base.update(over)
    return BlockSummary(**base)


class TestProtocole:
    def test_neuf_variantes_annoncees_et_quatre_hypotheses(self) -> None:
        assert list(VARIANTS) == [f"V{i}" for i in range(9)]
        assert set(HYPOTHESES) == {"H1", "H2", "H3", "H4"}
        assert all(h in HYPOTHESES for hypotheses in VARIANTS.values() for h in hypotheses)

    def test_valeurs_fixees_par_le_protocole(self) -> None:
        assert HYPOTHESES["H1"] == {"timeframe": "4h"}
        assert HYPOTHESES["H2"] == {"cooldown_seconds": 86_400}
        assert HYPOTHESES["H3"] == {"atr_stop_multiple": 2.5, "atr_take_profit_multiple": 5.0}
        assert HYPOTHESES["H4"] == {"regime_filter": True}
        assert MAX_DRAWDOWN_PCT == 15.0
        assert MIN_TRADES == {"learning": 60, "validation": 20, "final": 20}

    def test_v0_est_la_strategie_actuelle_sans_aucun_changement(self) -> None:
        config = variant_config("V0")
        assert (config.timeframe, config.cooldown_seconds, config.regime_filter) == ("1h", 0, False)
        assert config.atr_stop_multiple is None and config.atr_take_profit_multiple is None
        assert (config.fee_rate, config.slippage_rate) == (0.001, 0.0005)  # frais de l'étape 14

    def test_les_variantes_combinent_leurs_hypotheses(self) -> None:
        v7 = variant_config("V7")
        assert v7.timeframe == "4h" and v7.regime_filter is True
        assert (v7.atr_stop_multiple, v7.atr_take_profit_multiple) == (2.5, 5.0)
        assert v7.cooldown_seconds == 0
        v8 = variant_config("V8")
        assert v8.timeframe == "1h" and v8.cooldown_seconds == 86_400 and v8.regime_filter


class TestCriteres:
    def test_une_variante_qui_remplit_tout_reussit(self) -> None:
        assert passes(summary())

    @pytest.mark.parametrize(
        ("over", "failed"),
        [
            ({"return_pct": 1.0, "benchmark_scaled_pct": 2.0}, "a_bat_la_detention"),
            (
                {"return_pct": 2.0, "benchmark_scaled_pct": 2.0},
                "a_bat_la_detention",
            ),  # égalité : non
            ({"max_drawdown_pct": 15.0}, "b_perte_max_sous_15"),  # strictement sous 15
            ({"profit_factor": 1.1}, "c_facteur_de_profit"),  # strictement au-dessus de 1,1
            ({"profit_factor": 0.9}, "c_facteur_de_profit"),
            ({"trades": 59}, "d_assez_d_operations"),
        ],
    )
    def test_chaque_critere_est_eliminatoire(self, over, failed) -> None:
        result = criteria(summary(**over))
        assert result[failed] is False
        assert [name for name, ok in result.items() if not ok] == [failed]
        assert not passes(summary(**over))

    def test_les_seuils_d_operations_dependent_du_bloc(self) -> None:
        assert not passes(summary(block="learning", trades=59))
        assert passes(summary(block="validation", trades=20))
        assert not passes(summary(block="validation", trades=19))
        assert passes(summary(block="final", trades=20))

    def test_aucune_perte_du_tout_passe_le_facteur_de_profit_si_des_operations_existent(
        self,
    ) -> None:
        assert criteria(summary(profit_factor=None))["c_facteur_de_profit"] is True
        assert criteria(summary(profit_factor=None, trades=0))["c_facteur_de_profit"] is False


class TestDecision:
    def table(self, **blocks):
        return {
            variant: {"learning": learning, "validation": validation}
            for variant, (learning, validation) in blocks.items()
        }

    def test_il_faut_reussir_sur_les_deux_blocs_pas_sur_un_seul(self) -> None:
        good, bad = summary(), summary(return_pct=-5.0)
        table = self.table(
            V1=(good, good),
            V2=(good, bad),  # bon sur l'apprentissage seulement : c'est le hasard
            V3=(bad, good),
            V4=(bad, bad),
        )
        assert retained(table) == ["V1"]

    def test_aucune_variante_retenue_le_protocole_s_arrete(self) -> None:
        bad = summary(return_pct=-5.0)
        table = self.table(V0=(bad, bad), V1=(bad, bad))
        assert retained(table) == []
        assert select(table, []) is None

    def test_selection_par_la_pire_perte_maximale_des_deux_blocs(self) -> None:
        table = self.table(
            V1=(summary(max_drawdown_pct=10), summary("V1", "validation", max_drawdown_pct=14)),
            V2=(summary(max_drawdown_pct=12), summary("V2", "validation", max_drawdown_pct=9)),
        )
        # V1 : pire = 14 ; V2 : pire = 12 → V2
        assert select(table, ["V1", "V2"]) == "V2"

    def test_a_egalite_la_plus_simple(self) -> None:
        same = summary(max_drawdown_pct=10)
        table = self.table(V5=(same, same), V1=(same, same), V7=(same, same))
        assert select(table, ["V5", "V1", "V7"]) == "V1"  # 1 hypothèse contre 2 et 3


class TestTestFinal:
    @pytest.fixture
    def paths(self, tmp_path):
        return tmp_path / "study-results.json", tmp_path / "final-test.json"

    def write(self, results, selected):
        results.write_text(json.dumps({"selected": selected}), encoding="utf-8")

    def test_refuse_sans_etude_prealable(self, paths) -> None:
        with pytest.raises(FinalTestRefused, match="Lancez d'abord"):
            check_final_allowed("V1", *paths)

    def test_refuse_quand_aucune_variante_n_a_ete_retenue(self, paths) -> None:
        self.write(paths[0], None)
        with pytest.raises(FinalTestRefused, match="n'est pas ouvert"):
            check_final_allowed("V1", *paths)

    def test_refuse_une_variante_non_selectionnee(self, paths) -> None:
        self.write(paths[0], "V3")
        with pytest.raises(FinalTestRefused, match="V3"):
            check_final_allowed("V1", *paths)

    def test_accepte_la_variante_selectionnee(self, paths) -> None:
        self.write(paths[0], "V3")
        check_final_allowed("V3", *paths)  # ne lève pas

    def test_refuse_un_second_essai(self, paths) -> None:
        self.write(paths[0], "V3")
        paths[1].write_text("{}", encoding="utf-8")  # le test final a déjà été consulté
        with pytest.raises(FinalTestRefused, match="déjà été consulté"):
            check_final_allowed("V3", *paths)

    def test_le_fichier_du_test_final_est_dans_data(self) -> None:
        assert FINAL_PATH.as_posix() == "data/final-test.json"

"""
Setup Canali (Canonico) — Dispatcharr plugin.

Ricostruisce i canali a partire da una lista canonica (canali.json nei settings).
Match: per ogni stream, UPPERCASE + spazi singoli, assegnato al primo canale
canonico (ordinati dal piu lungo al piu corto) contenuto come sottostringa.
Prima del match: esclusione (nomi esatti) e alias (rename testo).
Ordinamento stream nel canale: qualita (4K>FHD>HD>SD), XC prima di STD.
Stream non riconosciuti -> gruppo "Altri Canali".

Usa direttamente l'ORM Django (nessuna chiamata HTTP, nessuna credenziale).
"""

import json
import re

_MULTISPACE = re.compile(r'\s+')


class Plugin:
    key = "setup_canali"
    name = "Setup Canali (Canonico)"
    version = "1.0.0"

    # ── Helpers di normalizzazione/match (identici allo script testato) ──────

    @staticmethod
    def _norm(name):
        return _MULTISPACE.sub(' ', (name or "").upper()).strip()

    @staticmethod
    def _quality_rank(stream_name):
        n = (stream_name or "").upper()
        if '4K' in n or 'UHD' in n or 'HEVC' in n or 'H265' in n or 'H 265' in n:
            return 0
        if 'FULL HD' in n or 'FHD' in n:
            return 1
        if re.search(r'\bHD\b', n):
            return 2
        if re.search(r'\bSD\b', n):
            return 4
        return 3

    @staticmethod
    def _apply_aliases(name, aliases):
        out = (name or "").upper()
        for cerca, sostituisci in aliases:
            if cerca in out:
                out = out.replace(cerca, sostituisci.upper())
        return out

    # ── Parsing dei settings (JSON nei campi testuali) ──────────────────────

    def _load_config(self, settings, logger):
        """Legge canali/alias/exclude dai settings (stringhe JSON)."""
        def _parse(raw, default, label):
            if raw is None or str(raw).strip() == "":
                return default
            try:
                return json.loads(raw)
            except Exception as e:
                logger.error(f"[setup_canali] '{label}' non e JSON valido: {e}")
                raise ValueError(f"Campo '{label}' non e JSON valido: {e}")

        canali = _parse(settings.get("canali_json"), {}, "canali_json")
        alias_raw = _parse(settings.get("alias_json"), {}, "alias_json")
        exclude_raw = _parse(settings.get("exclude_json"), [], "exclude_json")

        # canonici ordinati per lunghezza nome decrescente
        canonici = []
        for gruppo, lista in canali.items():
            for ch in lista:
                canonici.append((self._norm(ch), ch, gruppo))
        canonici.sort(key=lambda x: len(x[0]), reverse=True)

        aliases = [(k.upper(), v) for k, v in alias_raw.items()]
        aliases.sort(key=lambda x: len(x[0]), reverse=True)

        excludes = {str(x).upper().strip() for x in exclude_raw}

        return canali, canonici, aliases, excludes

    # ── Costruzione mappa canali dagli stream (via ORM) ─────────────────────

    def _build_map(self, settings, logger):
        from apps.channels.models import Stream

        canali_data, canonici, aliases, excludes = self._load_config(settings, logger)
        other_group = settings.get("other_group") or "\ud83d\udd35 Altri Canali"

        # tutti gli stream di account attivi (XC + STD)
        streams = (Stream.objects
                   .select_related("m3u_account")
                   .filter(m3u_account__is_active=True))

        channels_by_group = {}   # {gruppo: {nome_canale: [(sort_key, stream_obj)]}}
        assegnati = non_ass = esclusi = 0

        for st in streams.iterator():
            sname = st.name or ""
            if not sname:
                continue
            # salta stream senza URL riproducibile (a meno che sia un file locale)
            if not (st.url or st.local_file):
                continue
            if sname.upper().strip() in excludes:
                esclusi += 1
                continue

            sup = self._norm(self._apply_aliases(sname, aliases))
            match = None
            for cup, corig, grp in canonici:
                if cup and cup in sup:
                    match = (corig, grp)
                    break

            acct = st.m3u_account
            is_std = (getattr(acct, "account_type", "STD") or "STD").upper() != "XC"
            sort_key = (self._quality_rank(sname), 1 if is_std else 0)

            if match:
                corig, grp = match
                channels_by_group.setdefault(grp, {}).setdefault(corig, []).append((sort_key, st))
                assegnati += 1
            else:
                key = self._norm(sname)
                channels_by_group.setdefault(other_group, {}).setdefault(key, []).append((sort_key, st))
                non_ass += 1

        for grp in channels_by_group:
            for ch in channels_by_group[grp]:
                channels_by_group[grp][ch].sort(key=lambda x: x[0])

        # numerazione progressiva seguendo l'ordine di canali.json, poi Altri
        chno = {}
        n = 1
        for gruppo, lista in canali_data.items():
            for ch in lista:
                if gruppo in channels_by_group and ch in channels_by_group[gruppo]:
                    chno[(gruppo, ch)] = n
                    n += 1
        if other_group in channels_by_group:
            for ch in sorted(channels_by_group[other_group].keys()):
                chno[(other_group, ch)] = n
                n += 1

        stats = {"assegnati": assegnati, "non_assegnati": non_ass, "esclusi": esclusi,
                 "gruppi": len(channels_by_group),
                 "canali": sum(len(v) for v in channels_by_group.values())}
        return channels_by_group, chno, other_group, stats

    # ── Azioni ──────────────────────────────────────────────────────────────

    def _get_settings(self, context):
        # priorita: settings correnti dalla UI, poi persistiti
        s = {}
        s.update(context.get("settings", {}) or {})
        s.update(context.get("live_settings", {}) or {})
        return s

    def dry_run(self, params, context, logger):
        settings = self._get_settings(context)
        channels_by_group, chno, other_group, stats = self._build_map(settings, logger)

        righe = [
            f"ANTEPRIMA (nessuna modifica)",
            f"Stream assegnati: {stats['assegnati']}",
            f"In '{other_group}': {stats['non_assegnati']}",
            f"Esclusi: {stats['esclusi']}",
            f"Gruppi: {stats['gruppi']} | Canali: {stats['canali']}",
            "",
        ]
        # anteprima primi gruppi
        shown = 0
        for grp, chans in channels_by_group.items():
            if shown >= 6:
                righe.append("...")
                break
            righe.append(f"[{grp}] {len(chans)} canali")
            shown += 1
        return {"status": "ok", "message": "\n".join(righe)}

    def rebuild(self, params, context, logger):
        from django.db import transaction
        from apps.channels.models import (
            Channel, ChannelGroup, ChannelStream,
            ChannelProfile, ChannelProfileMembership, Logo,
        )
        from apps.accounts.models import User

        settings = self._get_settings(context)
        profile_name = settings.get("profile_name") or "IPTV"
        users_raw = (settings.get("users_list") or "").strip()
        # lista di username richiesti, vuoto = tutti
        wanted_users = [u.strip() for u in users_raw.split(",") if u.strip()] if users_raw else None

        channels_by_group, chno, other_group, stats = self._build_map(settings, logger)

        # cache dei loghi ESISTENTI (per url e per nome), come faceva lo script.
        # Non creiamo loghi nuovi: usiamo solo quelli gia nel Logo Manager.
        logo_by_url = {}
        logo_by_name = {}
        for lg in Logo.objects.all().iterator():
            if lg.url:
                logo_by_url[lg.url.strip()] = lg
            if lg.name:
                logo_by_name[lg.name.strip()] = lg
        logo_by_name_lower = {k.lower(): v for k, v in logo_by_name.items()}

        def _find_logo(stream_list, ch_name):
            """Replica find_logo_id: prima per url del primo stream, poi per nome."""
            if stream_list:
                first_url = (stream_list[0][1].logo_url or "").strip()
                if first_url and first_url in logo_by_url:
                    return logo_by_url[first_url]
            # prova per nome canale (esatto e case-insensitive)
            if ch_name in logo_by_name:
                return logo_by_name[ch_name]
            if ch_name.lower() in logo_by_name_lower:
                return logo_by_name_lower[ch_name.lower()]
            return None

        with transaction.atomic():
            # 1. reset: elimina il profilo esistente e TUTTI i canali (ricostruzione
            #    completa). ChannelStream e le membership vengono eliminate in
            #    cascata (on_delete=CASCADE sulle FK verso Channel).
            ChannelProfile.objects.filter(name=profile_name).delete()
            Channel.objects.all().delete()

            # 2. crea i gruppi mancanti
            group_ids = {}
            for grp in channels_by_group.keys():
                g, _ = ChannelGroup.objects.get_or_create(name=grp)
                group_ids[grp] = g.id

            # 3. crea i canali + logo + associa gli stream con l'ordine
            created = 0
            con_logo = 0
            con_tvg = 0
            created_channels = []
            for grp, chans in channels_by_group.items():
                for ch_name, stream_list in chans.items():
                    number = chno.get((grp, ch_name))
                    logo = _find_logo(stream_list, ch_name)
                    # eredita il tvg_id dal primo stream che ne ha uno valido
                    # (necessario per l'auto-match EPG). Gli stream sono gia
                    # ordinati per qualita, quindi si preferisce il migliore.
                    tvg_id = ""
                    for sk, st in stream_list:
                        if (st.tvg_id or "").strip():
                            tvg_id = st.tvg_id.strip()
                            break
                    ch = Channel.objects.create(
                        name=ch_name,
                        channel_number=number,
                        channel_group_id=group_ids[grp],
                        logo=logo,
                        tvg_id=tvg_id or None,
                    )
                    if logo:
                        con_logo += 1
                    if tvg_id:
                        con_tvg += 1
                    cs = [ChannelStream(channel=ch, stream=st, order=i)
                          for i, (sk, st) in enumerate(stream_list)]
                    ChannelStream.objects.bulk_create(cs)
                    created_channels.append(ch)
                    created += 1

            # 4. crea il profilo. Un signal post_save aggancia automaticamente
            #    tutti i canali esistenti; per robustezza garantiamo comunque le
            #    membership in modo esplicito (idempotente) e le abilitiamo.
            profile = ChannelProfile.objects.create(name=profile_name)
            existing = set(
                ChannelProfileMembership.objects
                .filter(channel_profile=profile)
                .values_list("channel_id", flat=True)
            )
            nuove = [
                ChannelProfileMembership(channel_profile=profile, channel=ch, enabled=True)
                for ch in created_channels if ch.id not in existing
            ]
            if nuove:
                ChannelProfileMembership.objects.bulk_create(nuove)
            # assicura che tutte le membership del profilo siano abilitate
            ChannelProfileMembership.objects.filter(channel_profile=profile).update(enabled=True)

            # 5. assegna il profilo agli utenti
            #    - se wanted_users e None (campo vuoto): assegna a TUTTI
            #    - altrimenti: assegna solo agli username elencati e RIMUOVE il
            #      profilo da tutti gli altri (selezione netta)
            assegnati_a = []
            non_trovati = []
            if wanted_users is None:
                for u in User.objects.all():
                    u.channel_profiles.add(profile)
                    assegnati_a.append(u.username)
            else:
                wanted_lower = {w.lower() for w in wanted_users}
                all_users = list(User.objects.all())
                for u in all_users:
                    if u.username.lower() in wanted_lower:
                        u.channel_profiles.add(profile)
                        assegnati_a.append(u.username)
                    else:
                        u.channel_profiles.remove(profile)
                esistenti_lower = {u.username.lower() for u in all_users}
                non_trovati = [w for w in wanted_users if w.lower() not in esistenti_lower]

        # 6. Auto-match EPG sui canali appena creati (fuori dalla transazione,
        #    cosi il task Celery vede i canali gia committati). Task in background.
        epg_msg = ""
        try:
            from apps.channels.tasks import match_selected_channels_epg
            new_ids = [ch.id for ch in created_channels]
            if new_ids:
                match_selected_channels_epg.delay(new_ids)
                epg_msg = f"Auto-match EPG avviato per {len(new_ids)} canali (in background)."
        except Exception as e:
            logger.warning(f"[setup_canali] EPG match non avviato: {e}")
            epg_msg = f"⚠️ Auto-match EPG non avviato: {e}"

        if wanted_users is None:
            users_msg = f"Profilo '{profile_name}' assegnato a TUTTI gli utenti ({len(assegnati_a)})."
        else:
            users_msg = f"Profilo '{profile_name}' assegnato a: {', '.join(assegnati_a) if assegnati_a else '(nessuno)'}."
            if non_trovati:
                users_msg += f"\n⚠️ Username non trovati: {', '.join(non_trovati)}"

        msg = (f"Ricostruzione completata.\n"
               f"Canali creati: {created} (con logo: {con_logo}, con tvg_id: {con_tvg})\n"
               f"Assegnati: {stats['assegnati']} | In '{other_group}': {stats['non_assegnati']} | Esclusi: {stats['esclusi']}\n"
               f"{users_msg}\n"
               f"{epg_msg}")
        logger.info(f"[setup_canali] {msg}")
        return {"status": "ok", "message": msg}

    # ── Dispatch ────────────────────────────────────────────────────────────

    def run(self, action, params, context):
        import logging
        logger = logging.getLogger("plugins.setup_canali")
        try:
            if action == "dry_run":
                return self.dry_run(params, context, logger)
            if action == "rebuild":
                return self.rebuild(params, context, logger)
            return {"status": "error", "message": f"Azione sconosciuta: {action}"}
        except Exception as e:
            logger.exception(f"[setup_canali] Errore nell'azione {action}")
            return {"status": "error", "message": f"Errore: {e}"}

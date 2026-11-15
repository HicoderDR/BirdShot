# SPDX-License-Identifier: AGPL-3.0-only
"""Exact-taxon public references. Only scientific names are sent to Wikimedia."""
import hashlib
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import io
import json
import logging
from pathlib import Path
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image, ImageOps

from comparison_prepare import atomic

VERSION = "wikidata-exact-species-commons-free-thumb480-v1"
CACHE_BUDGET = 128 * 1024 * 1024
USER_AGENT = "BirdShot-local-reference/1.0 (public species references; https://github.com/HicoderDR/BirdShot)"
API_HOSTS = {"www.wikidata.org", "commons.wikimedia.org"}
IMAGE_HOSTS = {"upload.wikimedia.org", "thumb.wikimedia.org"}
INAT_IMAGE_HOSTS = {"inaturalist-open-data.s3.amazonaws.com", "static.inaturalist.org"}
LOG = logging.getLogger("birdshot.species-reference")


class ReferenceError(RuntimeError):
    def __init__(self, message, reason_code=None, retry_after=30):
        super().__init__(message)
        self.reason_code = reason_code or ("upstreamBusy" if "maxlag" in message else "timeout" if "timeout" in message.lower() else "networkError")
        self.retry_after = retry_after
        self.partial = None


def retry_seconds(value, default=30):
    if not value:
        return default
    try:
        return max(1, int(value))
    except ValueError:
        try:
            return max(1, int(parsedate_to_datetime(value).timestamp() - time.time()))
        except (ValueError, TypeError, OverflowError):
            return default


def chinese_entry(result, name):
    result = dict(result)
    article = result.get("article")
    if article and article.get("language") == "zh":
        result["article"] = {**article, "kind": article.get("kind", "article")}
    else:
        if article:
            result["sourceArticle"] = article
        result["article"] = {"title": name, "language": "zh", "kind": "search",
                             "url": "https://zh.wikipedia.org/w/index.php?search=" + urllib.parse.quote(name, safe="")}
    if result["status"] == "error":
        reason = result.get("reasonCode") or ("upstreamBusy" if "maxlag" in result.get("errorType", "") else "networkError")
        result["reasonCode"] = reason
        result["message"] = {"upstreamBusy": "Wikimedia繁忙，请按提示稍后重试", "timeout": "公开资料连接超时，请稍后重试"}.get(reason, "公开资料网络暂不可用，请稍后重试")
    return result


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
    def handle_data(self, data):
        self.parts.append(data)


def plain(value):
    parser = PlainText()
    parser.feed(str(value or ""))
    return " ".join(" ".join(parser.parts).split())[:1000]


def official_url(url, hosts):
    try:
        parsed = urllib.parse.urlsplit(url)
        allowed = parsed.scheme == "https" and parsed.hostname in hosts and not parsed.username \
            and not parsed.password and parsed.port in {None, 443}
    except ValueError as error:
        raise ReferenceError("Invalid public reference URL") from error
    if not allowed:
        raise ReferenceError("Public reference host is not allowed")
    return url


class RestrictedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, hosts):
        self.hosts = hosts
    def redirect_request(self, request, fp, code, message, headers, new_url):
        official_url(new_url, self.hosts)
        count = getattr(request, "_reference_redirects", 0) + 1
        if count > 3:
            raise ReferenceError("Too many public reference redirects")
        redirected = super().redirect_request(request, fp, code, message, headers, new_url)
        if redirected:
            redirected._reference_redirects = count
        return redirected


def fetch(url, hosts, limit, image=False, with_headers=False):
    official_url(url, hosts)
    opener = urllib.request.build_opener(RestrictedRedirect(hosts))
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "image/*" if image else "application/json"})
    try:
        with opener.open(request, timeout=12) as response:
            official_url(response.url, hosts)
            mime = response.headers.get_content_type()
            if image and mime not in {"image/jpeg", "image/png", "image/webp"}:
                raise ReferenceError("Public thumbnail is not a supported raster image")
            length = response.headers.get("Content-Length")
            if length and int(length) > limit:
                raise ReferenceError("Public reference response exceeds the size limit")
            data = response.read(limit + 1)
            if len(data) > limit:
                raise ReferenceError("Public reference response exceeds the size limit")
            return (data, response.headers) if with_headers else data
    except urllib.error.HTTPError as error:
        busy = error.code in {429, 503}
        raise ReferenceError(f"Public reference HTTP {error.code}", "upstreamBusy" if busy else "upstreamHttpError",
                             retry_seconds(error.headers.get("Retry-After"))) from error
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError, ValueError) as error:
        timeout = isinstance(error, (socket.timeout, TimeoutError)) or isinstance(getattr(error, "reason", None), (socket.timeout, TimeoutError))
        raise ReferenceError(f"Public reference connection failed: {type(error).__name__}", "timeout" if timeout else "networkError") from error


def api(host, parameters):
    url = "https://" + host + "/w/api.php?" + urllib.parse.urlencode({**parameters, "format": "json", "maxlag": 30})
    try:
        payload, headers = fetch(url, API_HOSTS, 4 * 1024 * 1024, with_headers=True)
        result = json.loads(payload)
    except (ValueError, UnicodeError) as error:
        raise ReferenceError("Public reference API returned invalid JSON") from error
    if not isinstance(result, dict):
        raise ReferenceError("Public reference API returned an invalid object")
    if result.get("error"):
        code = str(result["error"].get("code", "unknown"))[:80]
        raise ReferenceError("Public reference API error: " + code, "upstreamBusy" if code in {"maxlag", "ratelimited", "readonly"} else "upstreamApiError",
                             retry_seconds(headers.get("Retry-After")))
    return result


def claims(entity, prop):
    rows = sorted(entity.get("claims", {}).get(prop, []), key=lambda row: row.get("rank") != "preferred")
    return [row["mainsnak"]["datavalue"]["value"] for row in rows
            if row.get("rank") != "deprecated" and row.get("mainsnak", {}).get("snaktype") == "value"
            and "datavalue" in row["mainsnak"]]


def exact_entity(name, entities):
    matches = [entity for entity in entities if name.casefold() in {str(value).casefold() for value in claims(entity, "P225")}
               and any(isinstance(value, dict) and value.get("id") == "Q7432" for value in claims(entity, "P105"))]
    return matches[0] if len(matches) == 1 else None


def permitted_license(metadata):
    value = lambda key: plain(metadata.get(key, {}).get("value", ""))
    name, url, author = value("LicenseShortName"), value("LicenseUrl"), value("Artist")
    if value("Restrictions"):
        return None
    match = re.fullmatch(r"CC (BY(?:-SA)?) ([1-4]\.\d)(?: [A-Za-z-]+)?", name)
    if match:
        parsed = urllib.parse.urlsplit(url)
        expected = "/licenses/" + match[1].lower() + "/" + match[2]
        if parsed.scheme in {"http", "https"} and parsed.hostname == "creativecommons.org" \
                and (parsed.path == expected or parsed.path.startswith(expected + "/")) and author:
            url = urllib.parse.urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, parsed.fragment))
            return {"license": name, "licenseUrl": url, "author": author}
    if name in {"CC0", "Public domain"} and value("Copyrighted").casefold() == "false":
        if not url:
            url = "https://commons.wikimedia.org/wiki/Commons:Licensing#Material_in_the_public_domain"
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme == "https" and parsed.hostname in {"creativecommons.org", "commons.wikimedia.org"}:
            return {"license": name, "licenseUrl": url, "author": author or "作者未注明（来源声明公有领域）"}
    return None


def exact_inat(name, rows):
    matches = [row for row in rows if row.get("name", "").casefold() == name.casefold()
               and row.get("rank") == "species" and row.get("is_active") is True
               and type(row.get("id")) is int and row["id"] > 0]
    return matches[0] if len(matches) == 1 else None


def inat_license(photo):
    code = photo.get("license_code")
    author = plain(photo.get("attribution"))
    # iNaturalist app/models/shared/license_module.rb defines CC 4.0 and CC0 1.0.
    licenses = {"cc-by": ("CC BY 4.0", "by/4.0/"), "cc-by-sa": ("CC BY-SA 4.0", "by-sa/4.0/"),
                "cc0": ("CC0 1.0", "zero/1.0/")}
    if code not in licenses or not author or photo.get("type") != "LocalPhoto":
        return None
    title, path = licenses[code]
    return {"license": title, "licenseCode": code, "licenseUrl": "https://creativecommons.org/" +
            ("publicdomain/" if code == "cc0" else "licenses/") + path, "author": author, "attribution": author}


def inat_api(route, parameters=None):
    if not re.fullmatch(r"taxa/(?:autocomplete|\d+)", route):
        raise ReferenceError("Invalid public taxon route", "invalidResponse")
    payload = fetch("https://api.inaturalist.org/v1/" + route + "?" + urllib.parse.urlencode(parameters or {}),
                    {"api.inaturalist.org"}, 4 * 1024 * 1024)
    try:
        result = json.loads(payload)
    except (ValueError, UnicodeError) as error:
        raise ReferenceError("iNaturalist returned invalid JSON", "invalidResponse") from error
    if not isinstance(result, dict) or not isinstance(result.get("results"), list):
        raise ReferenceError("iNaturalist returned an invalid taxon response", "invalidResponse")
    return result["results"]


class SpeciesReferences:
    def __init__(self, job, allowed_names):
        self.root = Path(job) / "revisions" / "species-reference-cache-v1"
        self.root.mkdir(parents=True, exist_ok=True)
        self.allowed = {name.casefold(): name for name in allowed_names}
        self.guard = threading.Lock()
        self.locks = {}
        self.network_slots = threading.BoundedSemaphore(2)

    def canonical(self, name):
        if not isinstance(name, str) or len(name) > 100 or not re.fullmatch(r"[A-Za-z][A-Za-z-]*(?: [A-Za-z][A-Za-z-]*){1,2}", name):
            raise ValueError("请输入当前候选中的有效物种学名")
        canonical = self.allowed.get(name.casefold())
        if canonical is None:
            raise ValueError("该学名不在本批鸟类候选中，不检索其他资料")
        return canonical

    def get(self, name):
        name = self.canonical(name)
        key = hashlib.sha256((VERSION + name.casefold()).encode()).hexdigest()
        path = self.root / (key + ".json")
        with self.guard:
            lock = self.locks.setdefault(key, threading.Lock())
        with lock:
            cached = None
            if path.exists():
                try:
                    cached = json.loads(path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, UnicodeError) as error:
                    raise ReferenceError("Public reference cache metadata is corrupt") from error
                image = cached["result"].get("image")
                available = not image
                if image:
                    try:
                        available = self.media(image["url"].rsplit("/", 1)[-1]).exists()
                    except (FileNotFoundError, ValueError):
                        LOG.warning("Species image cache missing; refreshing exact taxon %s", name)
                if cached["expiresAt"] > time.time() and available:
                    result = chinese_entry(cached["result"], name)
                    if result["status"] == "error":
                        result["retryAfterSeconds"] = max(1, int(cached["expiresAt"] - time.time()))
                        result["retryAt"] = cached["expiresAt"]
                    if cached.get("metadataVersion") != "zh-partial-backoff-v2":
                        atomic(path, {**cached, "metadataVersion": "zh-partial-backoff-v2", "result": result})
                    return result
            try:
                if not self.network_slots.acquire(timeout=30):
                    raise ReferenceError("Public reference requests are busy")
                try:
                    result = self._lookup_with_backup(name)
                finally:
                    self.network_slots.release()
                result = chinese_entry(result, name)
                ttl = 7 * 86400 if result["status"] == "ready" else 6 * 3600
                failures = 0
            except ReferenceError as error:
                LOG.warning("Species reference unavailable for %s: %s", name, error)
                previous = cached["result"] if cached and cached["result"].get("entityId") else None
                partial = error.partial or previous or {}
                failures = min(5, (cached or {}).get("failures", 0) + 1)
                ttl = max(error.retry_after, min(300, 30 * 2 ** (failures - 1)))
                result = chinese_entry({**partial, "status": "error", "scientificName": name,
                                       "article": partial.get("article"), "image": partial.get("image"),
                                       "message": None, "reasonCode": error.reason_code, "errorType": str(error),
                                       "partial": bool(partial), "stale": bool(previous and not error.partial),
                                       "retryAfterSeconds": ttl, "retryAt": time.time() + ttl}, name)
            atomic(path, {"version": VERSION, "metadataVersion": "zh-partial-backoff-v2", "failures": failures,
                          "expiresAt": time.time() + ttl, "fetchedAt": time.time(), "result": result})
            return result

    def media(self, filename):
        if not re.fullmatch(r"[0-9a-f]{64}\.jpg", filename):
            raise ValueError("Invalid registered reference image")
        path = self.root / filename
        if path.is_symlink() or not (self.root / (filename[:-4] + ".image.json")).is_file():
            raise FileNotFoundError("Reference image is not registered")
        return path

    def prune(self, protected):
        with self.guard:
            images = [path for path in self.root.glob("*.jpg")
                      if re.fullmatch(r"[a-f0-9]{64}\.jpg", path.name) and not path.is_symlink()
                      and path.is_file() and path.with_suffix(".image.json").is_file()]
            entries = sorted((path.stat().st_mtime_ns, path.stat().st_size, path) for path in images)
            total = sum(size for _, size, _ in entries)
            for _, size, path in entries:
                if total <= CACHE_BUDGET:
                    break
                if path == protected:
                    continue
                path.unlink()
                path.with_suffix(".image.json").unlink()
                total -= size

    def _lookup(self, name):
        result = {"status": "notFound", "scientificName": name, "article": None, "image": None,
                  "reasonCode": "noExactTaxon", "message": "未找到唯一且学名精确匹配的物种条目"}
        search = api("www.wikidata.org", {"action": "wbsearchentities", "search": name, "language": "en", "type": "item", "limit": 5})
        ids = [row["id"] for row in search.get("search", []) if re.fullmatch(r"Q\d+", row.get("id", ""))]
        if not ids:
            return result
        data = api("www.wikidata.org", {"action": "wbgetentities", "ids": "|".join(ids),
                                       "props": "info|claims|sitelinks", "sitefilter": "zhwiki|enwiki", "siteprops": "urls"})
        entity = exact_entity(name, list(data.get("entities", {}).values()))
        if entity is None:
            return result
        result.update(status="ready", message=None, reasonCode=None, entityId=entity["id"],
                      verifiedScientificName=name, entityRevision=entity.get("lastrevid"),
                      entityUrl="https://www.wikidata.org/wiki/" + entity["id"])
        for language in ["zh", "en"]:
            link = entity.get("sitelinks", {}).get(language + "wiki")
            if not link:
                continue
            url = link.get("url") or f"https://{language}.wikipedia.org/wiki/" + urllib.parse.quote(link["title"].replace(" ", "_"), safe="")
            official_url(url, {"zh.wikipedia.org", "en.wikipedia.org"})
            result["article"] = {"title": plain(link["title"]), "url": url, "language": language}
            break
        try:
            result["image"] = self._lookup_image(name, entity)
        except ReferenceError as error:
            error.partial = result
            raise
        if result["image"] is None:
            result.update(reasonCode="noLicensedImage", message="精确物种已核对；暂无许可与尺寸均合适的小型参考图")
        return chinese_entry(result, name)

    def _lookup_with_backup(self, name):
        primary_error = None
        try:
            result = self._lookup(name)
        except ReferenceError as error:
            primary_error = error
            result = error.partial or {"scientificName": name, "article": None, "image": None}
        if result.get("image"):
            return result
        try:
            image = self._inat_image(name)
        except ReferenceError as error:
            LOG.warning("iNaturalist backup unavailable for %s: %s", name, error)
            if primary_error:
                raise primary_error from error
            error.partial = result
            raise
        if image:
            return {**result, "status": "ready", "scientificName": name, "image": image,
                    "verifiedScientificName": name, "reasonCode": "fallbackImage",
                    "sourceWarning": primary_error.reason_code if primary_error else result.get("reasonCode"),
                    "message": "参考图来自精确物种匹配的iNaturalist授权备源；百科仍为中文词条或明确搜索"}
        if primary_error:
            raise primary_error
        return result

    def _inat_image(self, name):
        taxon = exact_inat(name, inat_api("taxa/autocomplete", {"q": name, "per_page": 3}))
        if taxon is None:
            return None
        taxon = exact_inat(name, inat_api("taxa/" + str(taxon["id"])))
        if taxon is None:
            return None
        for row in taxon.get("taxon_photos", [])[:20]:
            if row.get("taxon_id") != taxon["id"] or row.get("taxon", {}).get("name", "").casefold() != name.casefold():
                continue
            photo = row.get("photo", {})
            attribution = inat_license(photo)
            if not attribution or not isinstance(photo.get("id"), int) or not photo.get("medium_url"):
                continue
            remote = official_url(photo["medium_url"], INAT_IMAGE_HOSTS)
            source_url = "https://www.inaturalist.org/photos/" + str(photo["id"])
            metadata = {"provider": "iNaturalist", "taxonId": taxon["id"], "photoId": photo["id"],
                        "scientificName": name, "verifiedScientificName": name, "sourceUrl": source_url, **attribution}
            identity = {key: value for key, value in metadata.items() if key != "attribution"}
            key = hashlib.sha256(json.dumps([VERSION, identity, remote], sort_keys=True).encode()).hexdigest()
            path = self._cache_remote(key, remote, INAT_IMAGE_HOSTS, metadata)
            return {"url": "/species-media/" + path.name, **metadata}
        return None

    def _lookup_image(self, name, entity):
        for filename in claims(entity, "P18")[:3]:
            if not isinstance(filename, str) or len(filename) > 500:
                continue
            data = api("commons.wikimedia.org", {"action": "query", "titles": "File:" + filename, "redirects": 1,
                      "prop": "imageinfo", "iiprop": "url|extmetadata|sha1", "iiurlwidth": 480, "iiurlheight": 480})
            pages = list(data.get("query", {}).get("pages", {}).values())
            info = next((row["imageinfo"][0] for row in pages if row.get("imageinfo")), None)
            if not info or not info.get("thumburl"):
                continue
            dimensions = [info.get("thumbwidth"), info.get("thumbheight")]
            if not all(isinstance(value, int) and value > 0 for value in dimensions) or max(dimensions) > 640:
                continue
            attribution = permitted_license(info.get("extmetadata", {}))
            if not attribution:
                continue
            source_url = official_url(info["descriptionurl"], {"commons.wikimedia.org"})
            remote = official_url(info["thumburl"], IMAGE_HOSTS)
            image_key = hashlib.sha256(json.dumps([VERSION, entity["id"], filename, info.get("sha1"), attribution],
                                                  ensure_ascii=False, sort_keys=True).encode()).hexdigest()
            image_path = self._cache_remote(image_key, remote, IMAGE_HOSTS,
                {"provider": "Wikimedia Commons", "entityId": entity["id"], "scientificName": name, "file": filename,
                 "sourceUrl": source_url, "commonsSha1": info.get("sha1"), **attribution}, dimensions)
            return {"url": "/species-media/" + image_path.name, "sourceUrl": source_url, **attribution}
        return None

    def _cache_remote(self, key, remote, hosts, metadata, declared=None):
        path, registration = self.root / (key + ".jpg"), self.root / (key + ".image.json")
        if path.exists() and registration.exists():
            return path
        payload = fetch(remote, hosts, 3 * 1024 * 1024, image=True)
        try:
            with Image.open(io.BytesIO(payload)) as source:
                if source.format not in {"JPEG", "PNG", "WEBP"} or max(source.size) > 640:
                    raise ReferenceError("Public thumbnail is larger than the allowed small tier", "thumbnailSize")
                downloaded_size = list(source.size)
                image = ImageOps.exif_transpose(source)
                image.thumbnail((480, 480), Image.Resampling.LANCZOS)
                if image.mode in {"RGBA", "LA", "P"}:
                    rgba = image.convert("RGBA")
                    image = Image.new("RGB", rgba.size, "white")
                    image.paste(rgba, mask=rgba.getchannel("A"))
                elif image.mode != "RGB":
                    image = image.convert("RGB")
                output = io.BytesIO()
                image.save(output, "JPEG", quality=88, exif=b"",
                           icc_profile=source.info.get("icc_profile") if source.mode in {"RGB", "RGBA", "P"} else None)
            temporary = path.with_suffix(".part")
            temporary.write_bytes(output.getvalue())
            temporary.replace(path)
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            raise ReferenceError("Public thumbnail could not be safely decoded", "thumbnailDecode") from error
        atomic(registration, {**metadata, "remoteThumbnailUrl": remote, "declaredDimensions": declared,
                             "downloadedDimensions": downloaded_size, "downloadedBytes": len(payload),
                             "localDimensions": list(image.size), "localBytes": path.stat().st_size,
                             "changes": "仅取官方约500像素小图；缩至最长480像素，JPEG重编码、去EXIF，透明背景合成白色"})
        self.prune(path)
        return path

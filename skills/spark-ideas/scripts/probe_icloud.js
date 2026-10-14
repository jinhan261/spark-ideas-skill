// Foundation metadata only; no directory discovery, file contents, or download.
ObjC.import("Foundation");

function run(argv) {
    if (argv.length !== 1) return JSON.stringify({ ok: false });
    try {
        var url = $.NSURL.fileURLWithPath(argv[0]);
        var metadataError = Ref();
        var metadata = url.resourceValuesForKeysError($([$.NSURLIsUbiquitousItemKey, $.NSURLUbiquitousItemDownloadingStatusKey]), metadataError);
        if (metadata.isNil() || !metadataError[0].isNil()) return JSON.stringify({ ok: false });
        if (!$.NSFileManager.defaultManager.isUbiquitousItemAtURL(url)) {
            return JSON.stringify({ ok: true, ubiquitous: false });
        }
        var value = Ref();
        var error = Ref();
        if (!url.getResourceValueForKeyError(value, $.NSURLUbiquitousItemDownloadingStatusKey, error)) {
            return JSON.stringify({ ok: false });
        }
        return JSON.stringify({ ok: true, ubiquitous: true, status: ObjC.unwrap(value[0]) });
    } catch (_) {
        return JSON.stringify({ ok: false });
    }
}

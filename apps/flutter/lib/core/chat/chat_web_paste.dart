// Web-only: listens to the DOM `paste` event and extracts files/images
// from clipboardData — NO browser clipboard permission prompt (unlike
// navigator.clipboard.read(), which pasteboard uses on web).
//
// Conditional export: stub on IO (incl. widget tests).
export 'chat_web_paste_stub.dart'
    if (dart.library.html) 'chat_web_paste_web.dart';

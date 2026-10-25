/* Media viewers for any page: `import {registerMediaViewers} from
   '/static/js/components/media.js'` and call it before mount. Registers
   evidence-viewer, image-viewer, video-player, audio-player, pdf-viewer,
   sheet-viewer, media-thumb and actor-chip, and loads their stylesheet once. */
import {ActorChip} from './ActorChip.js';
import {AudioPlayer} from './AudioPlayer.js';
import {EvidenceViewer} from './EvidenceViewer.js';
import {ImageViewer} from './ImageViewer.js';
import {MediaThumb} from './MediaThumb.js';
import {PdfViewer} from './PdfViewer.js';
import {SheetViewer} from './SheetViewer.js';
import {VideoPlayer} from './VideoPlayer.js';

export function registerMediaViewers(app) {
  if (!document.querySelector('link[href="/static/css/media.css"]')) {
    const link = Object.assign(document.createElement('link'), {rel: 'stylesheet', href: '/static/css/media.css'});
    document.head.append(link);
  }
  return app.component('evidence-viewer', EvidenceViewer).component('image-viewer', ImageViewer).component('video-player', VideoPlayer)
    .component('audio-player', AudioPlayer).component('pdf-viewer', PdfViewer).component('sheet-viewer', SheetViewer).component('media-thumb', MediaThumb).component('actor-chip', ActorChip);
}

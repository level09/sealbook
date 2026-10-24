export const PREVIEWABLE = /^(image\/|video\/|audio\/|application\/pdf$)/;

export const EvidenceMedia = {
  props: {doc: {type:Object, required:true}, offset: {type:Number, default:0}, title: {type:Boolean, default:true}, link: {type:Boolean, default:true}},
  emits: ['revealed'],
  // Sensitive sources wait for the logged reveal; the server logs any other view itself.
  data() {return {url:this.doc.sensitive === false ? '/api/evidence/'+this.doc.sha256+'/content' : null, error:'', busy:false};},
  methods: {
    async reveal() {this.busy=true; try {this.url=(await axios.post('/api/evidence/'+this.doc.sha256+'/reveal',{item:{}})).data.url;this.$emit('revealed');} catch(err) {this.error=err.response?.data?.message || err.message;} finally {this.busy=false;}},
    seek(event) {event.target.currentTime=Math.max(0,this.offset/1000);}
  },
  template: `<section class="mb-3"><h4 v-if="title" class="text-title-small mb-2">{{doc.file}}</h4><p v-if="error" role="alert">{{error}}</p><v-btn v-if="!url" @click="reveal" :loading="busy" variant="outlined">Show sensitive item (access is logged)</v-btn><template v-else><img v-if="doc.mime === 'application/pdf'" :src="url+'?page='+(doc.page || 1)" :alt="doc.file+' page '+(doc.page || 1)" style="width:100%;max-height:800px;object-fit:contain"><img v-else-if="doc.mime.startsWith('image/')" :src="url" :alt="doc.file" style="width:100%;max-height:360px;object-fit:contain"><video v-else-if="doc.mime.startsWith('video/')" :src="url" controls preload="metadata" @loadedmetadata="seek" style="width:100%;max-height:360px"></video><audio v-else :src="url" controls preload="metadata" @loadedmetadata="seek" style="width:100%"></audio></template><v-btn v-if="link" :href="'/evidence/'+doc.sha256" variant="text" size="small" prepend-icon="ti ti-external-link" class="mt-1">Open evidence page</v-btn></section>`
};

// Both sides of a media match behind the same per-item reveal, with a readable summary.
export const EvidencePair = {
  components: {EvidenceMedia},
  props: {media: {type:Object, required:true}},
  computed: {
    offsets() {
      const m=this.media; if(m.picture?.sample_pairs?.length) return m.picture.sample_pairs[0];
      const offset=m.sound?.offset_ms || 0; return [Math.max(0,-offset),Math.max(0,offset)];
    }
  },
  template: `<div class="mb-3"><p v-if="media.picture" class="text-body-small mb-1">Picture: {{media.picture.query_coverage[0]}} of {{media.picture.query_coverage[1]}} unique samples matched.</p><p v-if="media.sound" class="text-body-small mb-1">Sound: {{media.sound.aligned_landmarks}} of {{media.sound.shared_landmarks}} shared landmarks agree on an offset of {{(media.sound.offset_ms/1000).toFixed(2)}} seconds.</p><v-row><v-col v-for="(side, i) in [media.left, media.right]" :key="side.sha256" cols="12" md="6"><evidence-media :doc="side" :offset="offsets[i]" :link="false"></evidence-media><v-btn :href="'/evidence/'+side.sha256" variant="text" size="small" prepend-icon="ti ti-external-link">Open evidence page</v-btn></v-col></v-row></div>`
};

// Business idempotency keys, not credentials. getRandomValues also works on HTTP previews.
export function loanRequestKey(cryptoProvider=globalThis.crypto){
 if(cryptoProvider?.randomUUID)return `loan-${cryptoProvider.randomUUID()}`;
 if(cryptoProvider?.getRandomValues){
  const bytes=cryptoProvider.getRandomValues(new Uint8Array(16));
  return `loan-${Array.from(bytes,byte=>byte.toString(16).padStart(2,'0')).join('')}`;
 }
 return `loan-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

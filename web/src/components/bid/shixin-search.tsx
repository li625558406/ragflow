export default function ShixinSearch() {
  return (
    <div className="h-full bg-white overflow-hidden">
      <iframe
        src="https://zxgk.court.gov.cn/shixin/"
        className="w-full h-full border-0"
        title="失信被执行人信息查询"
        sandbox="allow-scripts allow-forms allow-same-origin"
        loading="lazy"
      />
    </div>
  );
}

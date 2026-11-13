// Each portal follows its native parent while React keeps ownership of the section.
function cscNestedGroup({React, portal, section, parents, children}) {
  const [mount, setMount] = React.useState(null);
  React.useLayoutEffect(() => {
    let registry = window.cscSubsectionRegistry;
    if (!registry) {
      const groups = new Set();
      let frame = 0, sidebar;
      function place() {
        frame = 0;
        sidebar = document.querySelector('[data-app-action-sidebar-scroll]');
        for (const group of groups) {
          let row, host;
          if (group.parents.section) {
            host = sidebar?.querySelector('[data-sidebar-project-container-id="custom:' + CSS.escape(group.parents.section) + '"]');
            row = host?.matches('[data-app-action-sidebar-section-heading]') ? host :
              host?.querySelector('[data-app-action-sidebar-section-heading]');
          } else {
            for (const id of group.parents) {
              row = sidebar?.querySelector('[data-app-action-sidebar-project-id="' + CSS.escape(id) + '"]');
              if (row) break;
            }
            host = row?.closest('[role="listitem"][data-sidebar-project-container-id^="project:"],div[data-csc-project-group]') || row?.parentElement;
          }
          if (host && !group.node.contains(host)) {
            if (group.node.parentElement !== host) host.append(group.node);
            const collapsed = row?.getAttribute(group.parents.section ? 'data-app-action-sidebar-section-collapsed' : 'data-app-action-sidebar-project-collapsed') === 'true';
            if (group.node.hidden !== collapsed) group.node.hidden = collapsed;
            group.show(group.node);
          } else {
            group.node.remove();
            const projectsCollapsed = document.querySelector('[data-app-action-sidebar-section-heading="Projects"][data-app-action-sidebar-section-collapsed="true"]');
            group.show(group.parents.section || projectsCollapsed ? group.node : null);
          }
        }
      }
      const queue = () => { if (!frame) frame = requestAnimationFrame(place); };
      const observer = new MutationObserver(changes => {
        if (!sidebar?.isConnected || changes.some(change => sidebar.contains(change.target))) queue();
      });
      registry = window.cscSubsectionRegistry = {add(group) {
        if (!groups.size) observer.observe(document.documentElement, {subtree:true,childList:true,attributes:true,
          attributeFilter:['data-app-action-sidebar-project-collapsed','data-app-action-sidebar-section-collapsed','data-app-action-sidebar-project-id']});
        groups.add(group); queue();
        return () => {
          groups.delete(group);group.node.remove();
          if (!groups.size) {observer.disconnect();cancelAnimationFrame(frame);frame=0;}
        };
      }};
    }
    const node = document.createElement('div');
    node.dataset.cscSubsection = section;
    node.style.marginInlineStart = '12px';
    return registry.add({parents,node,show:setMount});
  }, [section, parents]);
  return mount ? portal(children,mount) : children;
}

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { ArrowLeft, Shield, FileText, ChevronRight } from 'lucide-react'
import slcLogo from '../../assets/slclogo.jpg'
import './PolicyPage.css'

const TABS = [
  { id: 'privacy', label: 'Privacy Policy', icon: Shield },
  { id: 'terms',   label: 'Vehicle Pass Terms', icon: FileText },
]

export default function PolicyPage() {
  const navigate = useNavigate()
  const [activeTab, setActiveTab] = useState('privacy')

  return (
    <div className="policy-page">
      {/* Header */}
      <header className="policy-header">
        <div className="policy-header-inner">
          <button className="policy-back-btn" onClick={() => navigate(-1)}>
            <ArrowLeft size={16} />
            <span>Return</span>
          </button>
          <div className="policy-header-logo-group">
            <img src={slcLogo} alt="SLC Logo" className="policy-header-logo" />
            <div className="policy-header-text">
              <span className="policy-header-title">SAINT LOUIS COLLEGE</span>
              <span className="policy-header-subtitle">Smart Parking and Vehicle Verification System</span>
            </div>
          </div>
        </div>
      </header>

      {/* Page Title */}
      <div className="policy-hero">
        <h1 className="policy-hero-title">Policies &amp; Terms</h1>
        <p className="policy-hero-sub">
          Saint Louis College — Data Privacy Policy and Vehicle Pass Terms &amp; Conditions
        </p>
      </div>

      {/* Tabs */}
      <div className="policy-tabs-bar">
        <div className="policy-tabs-inner">
          {TABS.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              className={`policy-tab-btn ${activeTab === id ? 'active' : ''}`}
              onClick={() => setActiveTab(id)}
            >
              <Icon size={15} />
              {label}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <main className="policy-main">
        <div className="policy-card">

          {/* ── PRIVACY POLICY TAB ── */}
          {activeTab === 'privacy' && (
            <div className="policy-content">
              <div className="policy-section-badge">Data Privacy Policy</div>
              <h2 className="policy-doc-title">
                Saint Louis College Privacy Policy for Applicants, Students, and Alumni
              </h2>
              <p className="policy-effective">
                In compliance with the Data Privacy Act of 2012 (Republic Act No. 10173)
              </p>

              <section className="policy-section">
                <h3>Introduction</h3>
                <p>
                  Welcome to Saint Louis College. SLC is committed to protecting the privacy and personal
                  data of our applicants, learners, and alumni. This Data Privacy Policy and Consent
                  outlines how we collect, use, process, and safeguard the personal information of all
                  individuals enrolled in our academic programs. We adhere to the principles of
                  transparency, fairness, and accountability in handling your data, in compliance with
                  the provisions of the Data Privacy Act of 2012 (RA 10173), its Implementing Rules and
                  Regulations, and other applicable laws.
                </p>
                <p>
                  By enrolling and continuing your studies at SLC, you acknowledge that you have read and
                  understood this Privacy Policy and that you give consent to Saint Louis College to
                  collect, process, store, and use your personal information.
                </p>
              </section>

              <section className="policy-section">
                <h3>Information We Collect, Acquire, or Generate</h3>
                <p>
                  Saint Louis College collects, acquires, or generates your personal data in various
                  forms, including written records, photographic and video images, and digital materials.
                  The type of information collected depends on your relationship with the College.
                </p>

                <div className="policy-sub-section">
                  <h4>1. Information Provided during Application for Admission and for Scholarship</h4>
                  <p>
                    When you apply for admission to Saint Louis College, we collect necessary personal data
                    to evaluate your application. This may include:
                  </p>
                  <ul>
                    <li>
                      <strong>Directory Information:</strong> Your full name, email address, mailing
                      address, mobile number, and other contact details, including those of emergency
                      contacts.
                    </li>
                    <li>
                      <strong>Personal Circumstances:</strong> Data concerning your family background,
                      relevant personal history, previous schools attended, academic performance,
                      disciplinary record, employment history, and medical records.
                    </li>
                    <li>
                      <strong>Application-Related Information:</strong> Any and all information obtained
                      through interviews, entrance examinations, or admission tests.
                    </li>
                  </ul>
                </div>

                <div className="policy-sub-section">
                  <h4>2. Information Collected or Generated After Enrollment</h4>
                  <p>
                    Upon enrollment and throughout your academic journey at SLC, we collect additional
                    information relevant to your studies and activities. This may include:
                  </p>
                  <ul>
                    <li><strong>Academic and Curricular Data:</strong> Information pertaining to your enrolled classes, scholastic performance, attendance records, and other academic undertakings.</li>
                    <li><strong>Co-curricular Engagements:</strong> Details of your participation in service learning, outreach activities, internships, or apprenticeships.</li>
                    <li><strong>Extra-curricular Activities:</strong> Information regarding your membership in student organizations, leadership roles, and participation in seminars, competitions, programs, outreach activities, and study tours.</li>
                    <li><strong>Disciplinary Records:</strong> Any incidents involving student behavior and corresponding sanctions.</li>
                    <li><strong>Visual Data:</strong> Pictures or videos of activities you participate in (via official documentation), or recordings from closed-circuit security television (CCTV) cameras installed within College premises for safety and security purposes.</li>
                  </ul>
                </div>

                <div className="policy-sub-section">
                  <h4>3. Unsolicited Information</h4>
                  <p>
                    There may be instances where personal information is sent to or received by SLC
                    without our prior request. In such cases, we will assess whether we have a legitimate
                    interest in retaining such information. If the information is not relevant to our
                    legitimate functions or purposes, we will immediately and securely dispose of it in a
                    manner that safeguards your privacy. Otherwise, it will be treated in the same manner
                    as information you provide to us directly, subject to the provisions of the DPA.
                  </p>
                </div>

                <div className="policy-sub-section">
                  <h4>4. Information of Other Individuals</h4>
                  <p>
                    If you provide us with personal data of other individuals (e.g., emergency contact
                    persons, family members), you certify that you have obtained the necessary consent
                    from these individuals for the disclosure of their personal data to SLC, in accordance
                    with the DPA.
                  </p>
                </div>
              </section>

              <section className="policy-section">
                <h3>How We Use Your Information</h3>
                <p>
                  Saint Louis College uses your personal data to pursue our legitimate interests as an
                  educational institution, including a variety of academic, administrative, research,
                  historical, and statistical purposes, to the extent permitted or required by law.
                  Specifically, we may use the information we collect for purposes such as:
                </p>
                <ul>
                  <li>Evaluating applications for admission to Saint Louis College.</li>
                  <li>Processing admission and registration of incoming, transfer, cross-registering, or non-degree students.</li>
                  <li>Recording, generating, and maintaining student records of academic, co-curricular, and extra-curricular progress.</li>
                  <li>Recording, storing, and evaluating student work, including but not limited to homework, quizzes, examinations, theses, dissertations, and research papers.</li>
                  <li>Recording, generating, and maintaining records, whether manually or electronically, of grades, academic history, class schedules, and participation in curricular, co-curricular, and extra-curricular activities.</li>
                  <li>Establishing and maintaining robust student information systems.</li>
                  <li>Facilitating the sharing of grades between and among faculty members, and other authorized personnel with a legitimate official need, for academic deliberations and evaluation of student performance.</li>
                  <li>Processing scholarship applications, grants, reporting to benefactors, and other forms of financial assistance.</li>
                  <li>Investigating incidents related to student behavior and implementing appropriate disciplinary measures.</li>
                  <li>Maintaining accurate directories and alumni records.</li>
                  <li>Compiling and generating reports for statistical analysis and research purposes to improve our educational services.</li>
                  <li>Providing essential services such as health and wellness programs, insurance, counseling, information technology support, library access, sports facilities, transportation, <strong>parking, campus mobility, and ensuring overall safety and security within the College.</strong></li>
                  <li>Managing and controlling access to College facilities and equipment.</li>
                  <li>Communicating official school announcements and urgent advisories.</li>
                  <li>Sharing marketing and promotional materials regarding legitimate College functions, events, projects, and activities.</li>
                  <li>Soliciting your participation in research and non-commercial surveys sanctioned by SLC for institutional development.</li>
                  <li>Soliciting your support, financial or otherwise, for College programs, projects, and events.</li>
                </ul>
                <p>
                  We consider the processing of your personal data for these purposes to be necessary for
                  the performance of our contractual obligations to you, for our compliance with legal
                  obligations (such as those mandated by the Commission on Higher Education), to protect
                  your vitally important interests (including your life and health), for the performance
                  of tasks we carry out in the public interest, or for the pursuit of the legitimate
                  interests of Saint Louis College or a third party. We acknowledge and are fully
                  committed to abiding by the DPA's stricter rules for the processing of sensitive
                  personal information and privileged information.
                </p>
                <p>
                  Should any specific use of your personal data require your explicit consent, we will
                  obtain it at the appropriate time and in a clear manner. Please be assured that Saint
                  Louis College will not subject your personal data to any automated decision-making
                  process without your prior explicit consent.
                </p>
              </section>

              <section className="policy-section">
                <h3>How We Share, Disclose, or Transfer Your Information</h3>
                <p>
                  To the extent permitted or required by law, and to uphold your interests and/or pursue
                  our legitimate interests as an educational institution, Saint Louis College may share,
                  disclose, or transfer your personal data to other persons or organizations for purposes
                  such as:
                </p>
                <ul>
                  <li><strong>Parental/Guardian Disclosure:</strong> Sharing of your personal data with your parents, guardians, or next of kin, as required by law, or on a need-to-know basis to promote your best interests, or to protect your health, safety, and security, or that of others.</li>
                  <li><strong>Scholarship and Financial Assistance:</strong> Sharing of some information with legitimate donors, funders, or benefactors for the administration of scholarships, grants, and other forms of assistance.</li>
                  <li><strong>Commencement Exercises:</strong> Distribution of the list of graduates and awardees in preparation for and during commencement exercises.</li>
                  <li><strong>Government Reporting:</strong> Reporting and/or disclosure of information to the National Privacy Commission (NPC) and other relevant government bodies or agencies, including but not limited to CHED, DepEd, DOST, Bureau of Immigration, DFA, CSC, BIR, PRC, Legal Education Board, and the Supreme Court, when required or allowed by law.</li>
                  <li><strong>Accreditation and Ranking:</strong> Sharing of information with recognized entities or organizations for accreditation and university ranking purposes (e.g., PAASCU, ISO 21001, and WURI).</li>
                  <li><strong>Competitions and Similar Events:</strong> Sharing of information with entities or organizations (e.g., CICM–PSN, PRISAA, and other legitimate sports bodies) for determining eligibility in sports or academic competitions, as well as other similar events.</li>
                  <li><strong>Legal Compliance:</strong> Complying with court orders, subpoenas, and/or other legal obligations.</li>
                  <li><strong>Institutional Development:</strong> Conducting internal research or surveys for purposes of institutional development and improvement.</li>
                  <li><strong>Promotional Publications:</strong> Publishing academic, co-curricular, and extra-curricular achievements and success, including names of awardees, in physical and electronic bulletin boards, the official College website, official social media sites, and College publications.</li>
                  <li><strong>Inter-School Information Sharing:</strong> Sharing your academic accomplishments or honors and co-curricular or extra-curricular achievements with schools you graduated from or were previously enrolled in, upon their legitimate request.</li>
                  <li><strong>Marketing and Promotion:</strong> Use of photos, videos, and other information to promote Saint Louis College, its activities, and events through marketing or advertising materials.</li>
                  <li><strong>Live-Streaming:</strong> Live-streaming of official College events.</li>
                  <li><strong>Journalistic Content:</strong> Publication of communications with journalistic content in official College publications and social media sites.</li>
                  <li><strong>Partnerships for Experiential Learning:</strong> Providing information such as class lists and photos to partner schools and industry partners, particularly for students undertaking experiential learning rotations as part of their curriculum.</li>
                  <li><strong>Academic Verification:</strong> Ensuring that the academic credentials you submit to potential employers are authentic and correctly represent your academic achievements.</li>
                </ul>
              </section>

              <section className="policy-section">
                <h3>How We Store and Retain Your Information</h3>
                <p>
                  Your personal data is stored and transmitted securely in a variety of paper and
                  electronic formats, including databases that may be shared between various units or
                  offices within Saint Louis College to ensure efficient operations. Access to your
                  personal data is strictly limited to authorized College personnel who have a legitimate
                  official need for such information to carry out their contractual duties and
                  responsibilities. Rest assured that our use of your personal data will be proportionate
                  and not excessive.
                </p>
                <p>
                  Unless otherwise provided by law or by appropriate SLC policies, we will retain your
                  relevant personal data indefinitely for historical and statistical purposes, particularly
                  for alumni records and academic transcripts. Where a specific retention period is
                  mandated by law and/or a College policy, all affected records will be securely disposed
                  of after such period, employing methods that prevent unauthorized access or disclosure.
                </p>
              </section>

              <section className="policy-section">
                <h3>Contact Us</h3>
                <p>
                  If you have any questions or concerns about this Privacy Policy, or if you wish to
                  exercise your rights under the Data Privacy Act, please contact the Data Protection
                  Officer of Saint Louis College at:{' '}
                  <a href="mailto:privacy@slc-sflu.edu.ph" className="policy-email-link">
                    privacy@slc-sflu.edu.ph
                  </a>{' '}
                  or visit the Data Protection Office.
                </p>
              </section>

              <section className="policy-section">
                <h3>Changes to this Privacy Policy</h3>
                <p>
                  Saint Louis College reserves the right to modify this Privacy Policy at any time. All
                  changes will be posted on the official Saint Louis College website and will take effect
                  immediately upon publication. We encourage you to review this policy periodically to
                  stay informed about how we are protecting your personal data.
                </p>
              </section>

              <div className="policy-consent-box">
                <h3>Student's Agreement and Consent</h3>
                <p>
                  To enroll and continue studying at Saint Louis College, you need to understand and agree
                  to this Privacy Policy. By registering for and using this system, you confirm that:
                </p>
                <ul>
                  <li>I acknowledge that I have read, understood, and agree to the provisions of the Saint Louis College Privacy Policy for Applicants, Students, and Alumni.</li>
                  <li>I understand and consent to the collection, processing, storage, retention, and sharing of my personal data by Saint Louis College, in accordance with the purposes and conditions outlined in this Privacy Policy and in compliance with the Data Privacy Act of 2012 (Republic Act No. 10173).</li>
                  <li>I affirm that all personal data I have provided to Saint Louis College are true, accurate, and complete. I further agree to promptly inform Saint Louis College of any changes to my personal data.</li>
                  <li>I acknowledge that I have the right to access, correct, or object to the processing of my personal data, as well as the right to lodge a complaint with the National Privacy Commission, subject to the provisions of the Data Privacy Act of 2012.</li>
                </ul>
              </div>
            </div>
          )}

          {/* ── VEHICLE PASS TERMS TAB ── */}
          {activeTab === 'terms' && (
            <div className="policy-content">
              <div className="policy-section-badge terms">Vehicle Pass Terms</div>
              <h2 className="policy-doc-title">
                Vehicle Pass Terms and Conditions
              </h2>
              <p className="policy-effective">
                Saint Louis College — Campus Driving and Parking Rules
              </p>

              <section className="policy-section">
                <h3>Application Requirement</h3>
                <p>
                  Applicants must submit <strong>(1) photocopy of the school-issued ID card</strong> or
                  the <strong>assessment or enrolment form</strong> together with the vehicle pass
                  application.
                </p>
              </section>

              <section className="policy-section">
                <h3>Agreement and Promise</h3>
                <p className="policy-agreement-intro">
                  <strong>
                    I agree and promise to abide by the terms and conditions anent my application for a
                    vehicle pass.
                  </strong>
                </p>

                <div className="policy-terms-list">
                  <div className="policy-term-item">
                    <span className="policy-term-bullet">•</span>
                    <p>
                      I understand that the vehicle pass is intended{' '}
                      <strong>ONLY TO ALLOW THE ENTRY OF MY VEHICLE IN THE CAMPUS.</strong> The College
                      does not guarantee the availability of parking spaces.
                    </p>
                  </div>
                  <div className="policy-term-item">
                    <span className="policy-term-bullet">•</span>
                    <p>
                      The application for a vehicle pass is subject to the approval or disapproval of the
                      Student Affairs Office.
                    </p>
                  </div>
                  <div className="policy-term-item">
                    <span className="policy-term-bullet">•</span>
                    <p>
                      To pay the Vehicle Pass fee of <strong>₱350.00</strong> at the{' '}
                      <strong>Accounting Office.</strong>
                    </p>
                  </div>
                </div>

                <p className="policy-subheading">As a responsible individual, I promise to:</p>

                <div className="policy-lettered-list">
                  {[
                    { letter: 'a', text: 'deactivate vehicle alarm while it is parked within the school premises;' },
                    { letter: 'b', text: 'see to it that my vehicle pass is placed on the dashboard, driver side, upon entry and during the entire stay inside the campus;' },
                    {
                      letter: 'c',
                      text: null,
                      jsx: (
                        <>
                          <strong>recognize the right of the school to decline the entry of my vehicle if the parking area is full;</strong>
                        </>
                      ),
                    },
                    { letter: 'd', text: 'be courteous to the school security and personnel and fellow parking space users;' },
                    { letter: 'e', text: 'allow the school security team to inspect my vehicle, as the need arises, before entry and when inside the campus;' },
                    { letter: 'f', text: 'strictly observe the speed limit of 10 kph within the campus;' },
                    {
                      letter: 'g',
                      text: null,
                      jsx: (
                        <>
                          park my vehicle at the designated parking area only so as not to obstruct the flow of traffic inside the campus. <strong>"NO DOUBLE PARKING";</strong>
                        </>
                      ),
                    },
                    { letter: 'h', text: 'not stay inside my vehicle while the engine is on and parked for safety and environmental reasons;' },
                    {
                      letter: 'i',
                      text: null,
                      jsx: <>observe the <strong>"No blowing of horn inside the campus"</strong> policy;</>,
                    },
                    { letter: 'j', text: 'avoid playing loud music or making unnecessary sounds using my vehicle upon entry;' },
                    {
                      letter: 'k',
                      text: null,
                      jsx: (
                        <>
                          strictly observe the <strong>"No Smoking"</strong> policy of the Institution.{' '}
                          <strong>Using e-cigarettes and/or vapes is not allowed;</strong>
                        </>
                      ),
                    },
                    {
                      letter: 'l',
                      text: null,
                      jsx: (
                        <>
                          properly lock and secure my vehicle while inside the campus as the College Administration is{' '}
                          <strong style={{ textDecoration: 'underline' }}>NOT LIABLE</strong> for anything
                          that may happen to the vehicle while it is parked inside the campus;
                        </>
                      ),
                    },
                    { letter: 'm', text: 'strictly observe traffic and/or coding scheme imposed;' },
                    {
                      letter: 'n',
                      text: null,
                      jsx: (
                        <>
                          follow the above terms and conditions and any violation committed thereto would
                          subject me to the following sanctions:
                        </>
                      ),
                    },
                  ].map(({ letter, text, jsx }) => (
                    <div key={letter} className="policy-lettered-item">
                      <span className="policy-letter">{letter}.</span>
                      <p>{jsx || text}</p>
                    </div>
                  ))}
                </div>
              </section>

              <section className="policy-section">
                <h3>Sanctions for Violations</h3>
                <p>
                  Any violation of the terms and conditions above shall subject the vehicle pass holder
                  to the following sanctions:
                </p>
                <div className="policy-sanctions">
                  <div className="policy-sanction-row first">
                    <span className="policy-sanction-label">First Offense</span>
                    <span className="policy-sanction-desc">
                      Confiscation of the vehicle pass for <strong>one (1) week.</strong>
                    </span>
                  </div>
                  <div className="policy-sanction-row second">
                    <span className="policy-sanction-label">Second Offense</span>
                    <span className="policy-sanction-desc">
                      Confiscation of the vehicle pass for <strong>two (2) weeks.</strong>
                    </span>
                  </div>
                  <div className="policy-sanction-row third">
                    <span className="policy-sanction-label">Third Offense</span>
                    <span className="policy-sanction-desc">
                      Confiscation of the vehicle pass.{' '}
                      <strong>Prohibition in securing a vehicle pass for the next school year.</strong>
                    </span>
                  </div>
                </div>
              </section>

              <div className="policy-consent-box">
                <h3>Acknowledgement</h3>
                <p>
                  By applying for and using a vehicle pass at Saint Louis College, I confirm that I have
                  read, understood, and agree to abide by all the terms and conditions stated above.
                  I understand that failure to comply may result in confiscation of my vehicle pass
                  and/or prohibition from securing a vehicle pass for the next school year.
                </p>
              </div>
            </div>
          )}
        </div>

        {/* Back  */}
        <div className="policy-footer">
          <button className="policy-footer-btn" onClick={() => navigate(-1)}>
            <ArrowLeft size={15} />
            Return
          </button>
          <span className="policy-footer-note">
            &copy; {new Date().getFullYear()} Saint Louis College. All rights reserved.
          </span>
        </div>
      </main>
    </div>
  )
}
